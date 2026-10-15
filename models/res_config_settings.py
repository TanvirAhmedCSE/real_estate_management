from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    re_late_fee_percent = fields.Float(
        "Late Fee (%)", config_parameter='real_estate_management.late_fee_percent', default=2.0)
    re_grace_days = fields.Integer(
        "Grace Period (days)", config_parameter='real_estate_management.grace_days', default=0)
    re_reminder_days = fields.Integer(
        "Reminder Before Due (days)", config_parameter='real_estate_management.reminder_days', default=3)
    re_cancel_deduction_percent = fields.Float(
        "Cancellation Deduction (%)",
        config_parameter='real_estate_management.cancel_deduction_percent', default=10.0)
