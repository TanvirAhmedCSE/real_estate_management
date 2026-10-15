from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class RealEstateRescheduleWizard(models.TransientModel):
    _name = 'real.estate.reschedule.wizard'
    _description = 'Reschedule Installments'

    booking_id = fields.Many2one('real.estate.booking', required=True, readonly=True)
    currency_id = fields.Many2one(related='booking_id.currency_id')
    pending_count = fields.Integer(compute='_compute_pending')
    pending_amount = fields.Monetary(compute='_compute_pending')
    new_count = fields.Integer("New No. of Installments", default=6, required=True)
    interval_months = fields.Integer("Interval (months)", default=1, required=True)
    first_date = fields.Date("First Due Date", required=True,
                             default=lambda self: fields.Date.context_today(self) + relativedelta(months=1))
    reason = fields.Text(required=True)

    def _pending_lines(self):
        return self.booking_id.line_ids.filtered(
            lambda l: l.line_type == 'installment' and l.state in ('due', 'overdue'))

    @api.depends('booking_id.line_ids.state')
    def _compute_pending(self):
        for wiz in self:
            lines = wiz._pending_lines()
            wiz.pending_count = len(lines)
            wiz.pending_amount = sum(lines.mapped('amount'))

    def action_reschedule(self):
        self.ensure_one()
        booking = self.booking_id
        if booking.state != 'installment':
            raise UserError(self.env._("Only running installment plans can be rescheduled."))
        if self.new_count < 1 or self.interval_months < 1:
            raise UserError(self.env._("Invalid installment count or interval."))
        lines = self._pending_lines()
        if not lines:
            raise UserError(self.env._("There are no pending installments to reschedule."))
        amount = sum(lines.mapped('amount'))
        lines._cancel_line()  # pending late fees are waived with the cancelled lines
        booking._generate_installment_lines(
            amount, self.new_count, self.interval_months, self.first_date,
            label=self.env._("Installment (Rescheduled)"))
        booking.message_post(body=self.env._(
            "Installments rescheduled: %(old)s pending line(s) replaced by %(new)s. Reason: %(reason)s",
            old=len(lines), new=self.new_count, reason=self.reason))
        return {'type': 'ir.actions.act_window_close'}
