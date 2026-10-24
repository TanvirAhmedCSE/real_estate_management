from odoo import api, fields, models


class RealEstateCommission(models.Model):
    _name = 'real.estate.commission'
    _description = 'Agent Commission'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    booking_id = fields.Many2one('real.estate.booking', required=True, ondelete='cascade', index=True)
    agent_id = fields.Many2one('res.partner', "Agent", required=True, index=True,
                               domain=[('is_agent', '=', True)])
    company_id = fields.Many2one(related='booking_id.company_id', store=True)
    currency_id = fields.Many2one(related='booking_id.currency_id', store=True)
    base_amount = fields.Monetary("Sale Price", related='booking_id.total_price', store=True)
    rate = fields.Float("Rate (%)", tracking=True)
    amount = fields.Monetary(compute='_compute_amount', store=True)
    state = fields.Selection(
        [('draft', "To Approve"), ('approved', "Approved"), ('paid', "Paid"), ('cancel', "Cancelled")],
        default='draft', tracking=True, copy=False)
    approved_date = fields.Date(readonly=True, copy=False)
    paid_date = fields.Date(readonly=True, copy=False)

    @api.depends('booking_id.name', 'agent_id.name')
    def _compute_name(self):
        for rec in self:
            rec.name = "%s - %s" % (rec.booking_id.name or '', rec.agent_id.name or '')

    @api.depends('base_amount', 'rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.currency_id.round(rec.base_amount * rec.rate / 100.0) \
                if rec.currency_id else rec.base_amount * rec.rate / 100.0

    def action_approve(self):
        self.filtered(lambda c: c.state == 'draft').write({
            'state': 'approved', 'approved_date': fields.Date.context_today(self)})

    def action_pay(self):
        self.filtered(lambda c: c.state == 'approved').write({
            'state': 'paid', 'paid_date': fields.Date.context_today(self)})

    def action_cancel(self):
        self.filtered(lambda c: c.state in ('draft', 'approved')).write({'state': 'cancel'})

    def action_draft(self):
        self.filtered(lambda c: c.state == 'cancel').write({'state': 'draft'})
