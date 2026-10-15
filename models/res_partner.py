from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_agent = fields.Boolean("Real Estate Sales Agent")
    commission_rate = fields.Float("Commission Rate (%)")
    re_booking_ids = fields.One2many('real.estate.booking', 'partner_id', string="Property Bookings")
    re_booking_count = fields.Integer(compute='_compute_re_booking_count')

    @api.depends('re_booking_ids')
    def _compute_re_booking_count(self):
        for partner in self:
            partner.re_booking_count = len(partner.sudo().re_booking_ids)

    def action_view_re_bookings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Bookings"),
            'res_model': 'real.estate.booking',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
        }
