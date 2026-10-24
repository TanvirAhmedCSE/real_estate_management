from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    re_booking_id = fields.Many2one('real.estate.booking', string="Property Booking",
                                    copy=False, index='btree_not_null', ondelete='set null')
    re_installment_line_id = fields.Many2one('real.estate.installment.line', string="Installment",
                                             copy=False, index='btree_not_null', ondelete='set null')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    is_re_late_fee = fields.Boolean(copy=False)
