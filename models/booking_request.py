import logging
from datetime import timezone

from dateutil import parser as date_parser

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)


class RealEstateBookingRequest(models.Model):
    _name = 'real.estate.booking.request'
    _description = 'Booking Request (from App)'
    _rec_name = 'customer_name'
    _order = 'request_date desc, id desc'

    _api_id_uniq = models.Constraint('UNIQUE(api_id)', "This request has already been imported.")

    api_id = fields.Char("App Request ID", required=True, readonly=True, copy=False, index=True)
    customer_name = fields.Char(required=True, readonly=True)
    customer_email = fields.Char(required=True, readonly=True)
    customer_phone = fields.Char(readonly=True)
    unit_id = fields.Many2one('real.estate.unit', "Unit", required=True, readonly=True,
                              ondelete='cascade', index=True)
    unit_state = fields.Selection(related='unit_id.state', string="Unit Status")
    request_date = fields.Datetime(readonly=True)
    state = fields.Selection(
        [('pending', "Pending"), ('converted', "Booking Created"), ('cancelled', "Cancelled")],
        default='pending', required=True, readonly=True, copy=False, index=True)
    booking_id = fields.Many2one('real.estate.booking', readonly=True, copy=False, ondelete='set null')
    push_pending = fields.Boolean(copy=False)

    
    # Sync with the app server
    
    @api.model
    def action_open_requests(self):
        """ Menu entry: refresh from the app server, then open the list. """
        action = self.env['ir.actions.act_window']._for_xml_id(
            'real_estate_management.action_booking_request')
        try:
            self.sudo()._sync_with_api()
        except Exception as e:
            _logger.warning("Booking request sync failed", exc_info=True)
            message = str(e) if isinstance(e, UserError) else self.env._("Could not reach the app server.")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': self.env._("Showing saved requests"),
                    'message': message,
                    'type': 'warning',
                    'next': action,
                },
            }
        return action

    @api.model
    def _sync_with_api(self):
        requests_ = self.sudo()
        requests_.search([('push_pending', '=', True)])._push_state()  # retry failed pushes first
        rows = self.env['real.estate.api']._call('GET', '/sync/requests')
        existing = {r.api_id: r for r in requests_.search([('api_id', 'in', [r['id'] for r in rows])])}
        unit_ids = set(requests_.env['real.estate.unit'].with_context(active_test=False).search(
            [('id', 'in', [r['unit_odoo_id'] for r in rows])]).ids)
        vals_list = []
        for row in rows:
            record = existing.get(row['id'])
            if record:
                # Fill the phone of requests imported before the phone field existed.
                if not record.customer_phone and row.get('customer_phone'):
                    record.customer_phone = row['customer_phone']
                # Odoo owns 'converted'; the app may only cancel a request that is still pending.
                if record.state == 'pending' and row['status'] == 'cancelled':
                    record.state = 'cancelled'
            elif row['unit_odoo_id'] in unit_ids:
                vals_list.append({
                    'api_id': row['id'],
                    'customer_name': row['customer_name'],
                    'customer_email': row['customer_email'],
                    'customer_phone': row.get('customer_phone') or False,
                    'unit_id': row['unit_odoo_id'],
                    'state': row['status'],
                    'request_date': date_parser.isoparse(row['created_at'])
                                    .astimezone(timezone.utc).replace(tzinfo=None),
                })
        if vals_list:
            requests_.create(vals_list)

    def _push_state(self):
        """ Report converted/cancelled back to the app server (retried later on failure). """
        api_client = self.env['real.estate.api']
        for record in self:
            try:
                api_client._call('PATCH', '/sync/requests/%s' % record.api_id, {'status': record.state})
                record.push_pending = False
            except Exception:
                _logger.warning("Could not push request %s to the app server", record.api_id, exc_info=True)
                record.push_pending = True

    
    # Buttons
    
    def action_create_booking(self):
        self.ensure_one()
        if self.state != 'pending':
            raise UserError(self.env._("This request has already been processed."))
        if self.unit_id.state != 'available':
            raise UserError(self.env._("Unit %s is not available.", self.unit_id.display_name))
        Partner = self.env['res.partner'].sudo()
        email = email_normalize(self.customer_email) or self.customer_email
        partner = Partner.search([('email_normalized', '=', email)], limit=1)
        if not partner:
            partner = Partner.create({
                'name': self.customer_name,
                'email': self.customer_email,
                'phone': self.customer_phone or False,
            })
        elif self.customer_phone and not partner.phone:
            partner.phone = self.customer_phone  # never overwrite an existing number
        booking = self.env['real.estate.booking'].create({
            'partner_id': partner.id,
            'unit_id': self.unit_id.id,
        })
        record = self.sudo()
        record.write({'state': 'converted', 'booking_id': booking.id})
        record._push_state()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'real.estate.booking',
            'res_id': booking.id,
            'view_mode': 'form',
        }

    def action_cancel(self):
        records = self.sudo().filtered(lambda r: r.state == 'pending')
        records.write({'state': 'cancelled'})
        records._push_state()
        return True