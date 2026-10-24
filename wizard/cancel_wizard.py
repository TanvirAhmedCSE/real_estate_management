from odoo import api, fields, models
from odoo.exceptions import UserError


class RealEstateCancelWizard(models.TransientModel):
    _name = 'real.estate.cancel.wizard'
    _description = 'Cancel Booking & Refund'

    def _default_percent(self):
        return float(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_management.cancel_deduction_percent', 10.0))

    booking_id = fields.Many2one('real.estate.booking', required=True, readonly=True)
    currency_id = fields.Many2one(related='booking_id.currency_id')
    paid_amount = fields.Monetary("Total Paid", compute='_compute_refund')
    deduction_percent = fields.Float("Deduction (%)", default=_default_percent)
    deduction_amount = fields.Monetary(compute='_compute_refund')
    refund_amount = fields.Monetary("Refund Amount", compute='_compute_refund')
    reason = fields.Text(required=True)

    @api.depends('booking_id', 'booking_id.line_ids.state', 'deduction_percent')
    def _compute_refund(self):
        for wiz in self:
            paid = sum(wiz.booking_id.line_ids.filtered(lambda l: l.state == 'paid').mapped('paid_amount'))
            deduction = wiz.currency_id.round(paid * wiz.deduction_percent / 100.0)
            wiz.paid_amount = paid
            wiz.deduction_amount = deduction
            wiz.refund_amount = max(paid - deduction, 0.0)

    def action_cancel_booking(self):
        self.ensure_one()
        booking = self.booking_id
        if booking.state not in ('booked', 'agreement', 'installment'):
            raise UserError(self.env._("This booking cannot be cancelled."))
        booking.line_ids.filtered(lambda l: l.state in ('due', 'overdue'))._cancel_line()
        booking.unit_id.sudo().write({'state': 'available'})
        booking.commission_ids.filtered(lambda c: c.state in ('draft', 'approved')).sudo().action_cancel()
        booking.write({
            'state': 'cancel',
            'cancel_date': fields.Date.context_today(self),
            'cancel_reason': self.reason,
            'refund_amount': self.refund_amount,
        })
        booking.message_post(body=self.env._(
            "Booking cancelled. Paid: %(paid)s, deduction: %(ded)s, refund due to customer: %(refund)s. Reason: %(reason)s",
            paid=self.paid_amount, ded=self.deduction_amount,
            refund=self.refund_amount, reason=self.reason))
        return {'type': 'ir.actions.act_window_close'}
