import logging

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.image import image_process

_logger = logging.getLogger(__name__)

# Changing any of these marks the unit for (re)sending to the app server.
SYNC_FIELDS = {'name', 'project_id', 'building_id', 'unit_type', 'floor', 'size', 'size_unit',
               'price', 'state', 'active'}

IMAGE_MIMETYPES = ('image/jpeg', 'image/png', 'image/webp')
IMAGE_MAX_SIZE = (2000, 2000)  # bigger photos are scaled down before they go to Cloudinary


class RealEstateUnit(models.Model):
    _name = 'real.estate.unit'
    _description = 'Property Unit (Flat / Plot / Shop)'
    _inherit = ['mail.thread']
    _order = 'project_id, building_id, name'

    name = fields.Char("Unit No.", required=True, tracking=True)
    project_id = fields.Many2one(
        'real.estate.project', required=True, index=True, ondelete='restrict', tracking=True)
    building_id = fields.Many2one(
        'real.estate.building', domain="[('project_id', '=', project_id)]", ondelete='restrict')
    unit_type = fields.Selection(
        [('flat', "Flat"), ('plot', "Plot"), ('shop', "Shop")],
        default='flat', required=True)
    size = fields.Float()
    size_unit = fields.Selection(
        [('sqft', "Sq. Ft."), ('katha', "Katha"), ('decimal', "Decimal")],
        default='sqft', required=True)
    floor = fields.Char()
    price = fields.Monetary(tracking=True)
    state = fields.Selection(
        [('available', "Available"), ('booked', "Booked"), ('sold', "Sold")],
        default='available', required=True, tracking=True, copy=False)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    booking_ids = fields.One2many('real.estate.booking', 'unit_id')
    booking_id = fields.Many2one('real.estate.booking', string="Active Booking", compute='_compute_booking')
    buyer_id = fields.Many2one('res.partner', compute='_compute_booking')
    description = fields.Text()
    sync_dirty = fields.Boolean(default=True, copy=False, index=True)
    image_attachment_ids = fields.Many2many(
        'ir.attachment', 'real_estate_unit_image_rel', 'unit_id', 'attachment_id',
        string="Images", copy=False)

    @api.depends('booking_ids.state', 'booking_ids.partner_id')
    def _compute_booking(self):
        for unit in self:
            active = unit.booking_ids.filtered(lambda b: b.state not in ('draft', 'cancel'))[:1]
            unit.booking_id = active
            unit.buyer_id = active.partner_id

    @api.depends('name', 'project_id.name', 'building_id.name')
    def _compute_display_name(self):
        for unit in self:
            unit.display_name = ' / '.join(
                filter(None, [unit.project_id.name, unit.building_id.name, unit.name]))

    @api.constrains('name', 'project_id', 'building_id')
    def _check_unique_unit(self):
        for unit in self:
            dup = self.with_context(active_test=False).search_count([
                ('id', '!=', unit.id), ('name', '=', unit.name),
                ('project_id', '=', unit.project_id.id),
                ('building_id', '=', unit.building_id.id),
            ])
            if dup:
                raise ValidationError(self.env._("Unit %s already exists in this project/building.", unit.name))

    @api.constrains('building_id', 'project_id')
    def _check_building_project(self):
        for unit in self:
            if unit.building_id and unit.building_id.project_id != unit.project_id:
                raise ValidationError(self.env._("The building must belong to the selected project."))

    
    # Images (uploaded as attachments by the form, stored on Cloudinary)
    
    def _upload_new_images(self):
        """ Send freshly attached images to Cloudinary; the first image is the cover. """
        work = []
        for unit in self:
            pending = unit.image_attachment_ids.sudo().filtered(
                lambda a: not a.re_cloud_public_id).sorted('id')
            for att in pending:
                if att.mimetype not in IMAGE_MIMETYPES:
                    raise UserError(self.env._("%s is not a JPG, PNG or WEBP image.", att.name))
            if pending:
                work.append((unit, pending))
        if not work:
            return
        cloud = self.env['real.estate.cloudinary']
        uploaded = []
        try:
            for unit, attachments in work:
                folder = 'real_estate/unit_%s' % unit.id
                for att in attachments:
                    try:
                        raw = image_process(att.raw, size=IMAGE_MAX_SIZE)
                    except Exception as e:
                        raise UserError(self.env._("%s could not be read as an image.", att.name)) from e
                    result = cloud._upload(raw, att.name, folder)
                    uploaded.append(result['public_id'])
                    att.write({
                        're_cloud_url': result['url'],
                        're_cloud_public_id': result['public_id'],
                        # an attachment without a record is invisible to other users
                        'res_model': self._name,
                        'res_id': unit.id,
                    })
        except Exception:
            for public_id in uploaded:  # do not leave orphans behind when the save fails
                try:
                    cloud._destroy(public_id)
                except Exception:
                    _logger.warning("Could not remove %s from Cloudinary.", public_id, exc_info=True)
            raise

    def _discard_images(self, attachments):
        """ Delete removed images; the Cloudinary copy goes away after commit. """
        attachments = attachments.sudo()
        public_ids = [pid for pid in attachments.mapped('re_cloud_public_id') if pid]
        attachments.unlink()
        if not public_ids:
            return
        registry = self.env.registry

        def destroy():
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                for public_id in public_ids:
                    try:
                        env['real.estate.cloudinary']._destroy(public_id)
                    except Exception:
                        _logger.warning("Could not remove %s from Cloudinary.", public_id, exc_info=True)

        self.env.cr.postcommit.add(destroy)

    
    # App server sync
    
    @api.model_create_multi
    def create(self, vals_list):
        units = super().create(vals_list)
        units._upload_new_images()
        units._schedule_push()
        return units

    def write(self, vals):
        images_changed = 'image_attachment_ids' in vals
        images_before = self.image_attachment_ids if images_changed else self.env['ir.attachment']
        if images_changed or SYNC_FIELDS.intersection(vals):
            vals = dict(vals, sync_dirty=True)
        res = super().write(vals)
        if images_changed:
            self._upload_new_images()
            self._discard_images(images_before - self.image_attachment_ids)
        if vals.get('sync_dirty'):
            self._schedule_push()
        return res

    def unlink(self):
        payloads = [dict(unit._api_payload(), active=False) for unit in self]
        self._discard_images(self.image_attachment_ids)
        res = super().unlink()
        if payloads and self.env['real.estate.api']._is_configured():
            registry = self.env.registry

            def push_removed():
                with registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    try:
                        env['real.estate.api']._call('POST', '/sync/units', {'units': payloads})
                    except Exception:
                        _logger.warning("Could not hide deleted units in the app.", exc_info=True)

            self.env.cr.postcommit.add(push_removed)
        return res

    def _api_payload(self):
        self.ensure_one()

        def label(fname):
            return dict(self._fields[fname]._description_selection(self.env)).get(self[fname])

        return {
            'odoo_id': self.id,
            'name': self.name,
            'project': self.project_id.name,
            'building': self.building_id.name,
            'unit_type': label('unit_type'),
            'floor': self.floor,
            'size': self.size,
            'size_unit': label('size_unit'),
            'price': self.price,
            'state': self.state,
            'active': self.active,
            'images': [
                {'url': att.re_cloud_url, 'public_id': att.re_cloud_public_id}
                for att in self.sudo().image_attachment_ids.sorted('id') if att.re_cloud_public_id],
        }

    def _schedule_push(self):
        """ Send after commit so the app never sees data that was rolled back. """
        if not self.env['real.estate.api']._is_configured():
            return
        ids, registry = self.ids, self.env.registry

        def push():
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['real.estate.unit'].browse(ids)._push_to_api()

        self.env.cr.postcommit.add(push)

    def _push_to_api(self):
        units = self.filtered('sync_dirty')
        if not units:
            return True
        try:
            self.env['real.estate.api']._call(
                'POST', '/sync/units', {'units': [unit._api_payload() for unit in units]})
        except Exception:
            _logger.warning("Unit sync to the app server failed; the cron will retry.", exc_info=True)
            return False
        units.write({'sync_dirty': False})
        return True

    @api.model
    def _cron_push_units(self):
        if self.env['real.estate.api']._is_configured():
            self.with_context(active_test=False).search(
                [('sync_dirty', '=', True)], limit=200)._push_to_api()

    def action_sync_to_app(self):
        self.sudo().write({'sync_dirty': True})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': self.env._("Units queued for the app."), 'type': 'info'},
        }