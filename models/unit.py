from odoo import api, fields, models
from odoo.exceptions import ValidationError


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
