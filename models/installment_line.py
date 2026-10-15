import logging
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM = 'real_estate_management.'


class RealEstateInstallmentLine(models.Model):
    _name = 'real.estate.installment.line'
    _description = 'Installment Line'
    _inherit = ['mail.thread']
    _order = 'booking_id, sequence, due_date, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    booking_id = fields.Many2one('real.estate.booking', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='booking_id.partner_id', store=True, index=True)
    agent_id = fields.Many2one(related='booking_id.agent_id', store=True)
    project_id = fields.Many2one(related='booking_id.project_id', store=True)
    unit_id = fields.Many2one(related='booking_id.unit_id', store=True)
    company_id = fields.Many2one(related='booking_id.company_id', store=True)
    currency_id = fields.Many2one(related='booking_id.currency_id', store=True)

    line_type = fields.Selection([
        ('booking', "Booking Money"),
        ('down', "Down Payment"),
        ('installment', "Installment"),
        ('handover', "Handover Payment"),
    ], required=True, default='installment')
    due_date = fields.Date(required=True, tracking=True, index=True)
    amount = fields.Monetary(required=True, tracking=True)
    late_fee = fields.Monetary(tracking=True, copy=False)
    total_amount = fields.Monetary(compute='_compute_total_amount', store=True)

    cancelled = fields.Boolean(copy=False)
    is_overdue = fields.Boolean(copy=False)
    state = fields.Selection(
        [('due', "Due"), ('overdue', "Overdue"), ('paid', "Paid"), ('cancel', "Cancelled")],
        compute='_compute_state', store=True, tracking=True, index=True)
    paid_date = fields.Date(compute='_compute_paid_date', store=True)
    paid_amount = fields.Monetary(compute='_compute_total_amount', store=True)

    invoice_id = fields.Many2one('account.move', copy=False, readonly=True, index='btree_not_null')
    payment_link = fields.Char(compute='_compute_payment_link')
    reminder_sent = fields.Boolean(copy=False)
    overdue_notified = fields.Boolean(copy=False)
    receipt_sent = fields.Boolean(copy=False)

    
    # Computes
    
    @api.depends('amount', 'late_fee', 'state')
    def _compute_total_amount(self):
        for line in self:
            line.total_amount = line.amount + line.late_fee
            line.paid_amount = line.amount + line.late_fee if line.state == 'paid' else 0.0

    @api.depends('cancelled', 'is_overdue', 'invoice_id.payment_state')
    def _compute_state(self):
        for line in self:
            if line.cancelled:
                line.state = 'cancel'
            elif line.invoice_id.payment_state in ('paid', 'in_payment'):
                line.state = 'paid'
            elif line.is_overdue:
                line.state = 'overdue'
            else:
                line.state = 'due'

    @api.depends('state')
    def _compute_paid_date(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.paid_date = (line.paid_date or today) if line.state == 'paid' else False

    def _compute_payment_link(self):
        for line in self:
            inv = line.sudo().invoice_id
            if inv and inv.state == 'posted' and line.state in ('due', 'overdue'):
                line.payment_link = inv.get_base_url() + inv.get_portal_url()
            else:
                line.payment_link = False
    
    def _get_report_base_filename(self):
        self.ensure_one()
        return "Receipt - %s - %s" % (self.booking_id.name, self.name)

    
    # Invoicing
    
    def _prepare_invoice_vals(self):
        self.ensure_one()
        lines = [Command.create({
            'name': "%s - %s" % (self.booking_id.name, self.name),
            'quantity': 1,
            'price_unit': self.amount,
        })]
        if self.late_fee:
            lines.append(Command.create({
                'name': self.env._("Late fee - %s", self.name),
                'quantity': 1,
                'price_unit': self.late_fee,
                'is_re_late_fee': True,
            }))
        return {
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_date_due': self.due_date,
            'invoice_origin': self.booking_id.name,
            'ref': "%s / %s" % (self.booking_id.name, self.name),
            're_booking_id': self.booking_id.id,
            're_installment_line_id': self.id,
            'invoice_line_ids': lines,
        }

    def _ensure_invoice(self):
        """ Create and post the customer invoice of the installment (idempotent). """
        for line in self.sudo():
            if line.cancelled or line.state == 'paid':
                continue
            inv = line.invoice_id
            if inv and inv.state == 'cancel':
                inv = False
            if not inv:
                inv = self.env['account.move'].sudo().create(line._prepare_invoice_vals())
                line.invoice_id = inv
            if inv.state == 'draft':
                inv.action_post()
        return True

    def _sync_invoice_late_fee(self):
        """ Reflect the line's late fee on its unpaid invoice (reset to draft, edit, repost). """
        for line in self.sudo():
            inv = line.invoice_id
            if not inv or inv.state == 'cancel' or inv.payment_state != 'not_paid':
                continue
            was_posted = inv.state == 'posted'
            if was_posted:
                inv.button_draft()
            commands = [Command.delete(l.id) for l in inv.invoice_line_ids.filtered('is_re_late_fee')]
            if line.late_fee:
                commands.append(Command.create({
                    'name': self.env._("Late fee - %s", line.name),
                    'quantity': 1,
                    'price_unit': line.late_fee,
                    'is_re_late_fee': True,
                }))
            if commands:
                inv.write({'invoice_line_ids': commands})
            if was_posted:
                inv.action_post()

    def _cancel_line(self):
        for line in self.sudo():
            if line.state == 'paid':
                continue
            inv = line.invoice_id
            if inv and inv.state != 'cancel':
                if inv.payment_state not in ('not_paid',):
                    raise UserError(self.env._(
                        "%s already has a (partial) payment. Reverse the payment first.", line.name))
                if inv.state == 'posted':
                    inv.button_draft()
                inv.button_cancel()
            line.cancelled = True

    
    # Buttons
    
    def action_create_invoice(self):
        self._ensure_invoice()

    def action_view_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(self.env._("No invoice yet."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
        }

    def action_waive_late_fee(self):
        for line in self:
            if line.late_fee:
                line.late_fee = 0.0
                line._sync_invoice_late_fee()
                line.message_post(body=self.env._("Late fee waived by %s.", self.env.user.name))

    def action_send_reminder(self):
        for line in self:
            line._ensure_invoice()
            line._send_template('real_estate_management.mail_template_installment_reminder')

    def action_send_receipt(self):
        for line in self:
            if line.state != 'paid':
                raise UserError(self.env._("Receipts can only be sent for paid installments."))
            line._send_template('real_estate_management.mail_template_payment_receipt')
            line.receipt_sent = True

    def _send_template(self, xmlid):
        self.ensure_one()
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template or not self.partner_id.email:
            return False
        template.sudo().send_mail(self.id, force_send=True, email_layout_xmlid='mail.mail_notification_light')
        return True

    def _schedule_activity(self, summary):
        for line in self.sudo():
            user = line.booking_id.user_id or self.env.ref('base.user_admin')
            line.booking_id.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=line.due_date if line.due_date >= fields.Date.today() else fields.Date.today(),
                summary=summary,
                note="%s - %s" % (line.booking_id.name, line.name),
                user_id=user.id,
            )

    
    # Crons
    
    @api.model
    def _cron_installment_reminders(self):
        """ Daily: upcoming-due reminders, then overdue flag + late fee + notification. """
        icp = self.env['ir.config_parameter'].sudo()
        reminder_days = int(icp.get_param(PARAM + 'reminder_days', 3))
        grace_days = int(icp.get_param(PARAM + 'grace_days', 0))
        fee_percent = float(icp.get_param(PARAM + 'late_fee_percent', 2.0))
        today = fields.Date.context_today(self)
        active_booking = [('booking_id.state', 'in', ('booked', 'agreement', 'installment'))]

        # 1. Reminders
        upcoming = self.search([
            ('state', '=', 'due'), ('reminder_sent', '=', False), ('line_type', '!=', 'booking'),
            ('due_date', '>=', today), ('due_date', '<=', today + timedelta(days=reminder_days)),
        ] + active_booking)
        for line in upcoming:
            try:
                with self.env.cr.savepoint():
                    line._ensure_invoice()
                    line._send_template('real_estate_management.mail_template_installment_reminder')
                    line._schedule_activity(self.env._("Installment due soon"))
                    line.reminder_sent = True
            except Exception:
                _logger.exception("Installment reminder failed for line %s", line.id)

        # 2. Overdue
        overdue = self.search([
            ('state', '=', 'due'), ('due_date', '<', today - timedelta(days=grace_days)),
        ] + active_booking)
        for line in overdue:
            try:
                with self.env.cr.savepoint():
                    vals = {'is_overdue': True}
                    if fee_percent and not line.late_fee:
                        vals['late_fee'] = line.currency_id.round(line.amount * fee_percent / 100.0)
                    line.write(vals)
                    if line.invoice_id:
                        line._sync_invoice_late_fee()
                    else:
                        line._ensure_invoice()
                    if not line.overdue_notified:
                        line._send_template('real_estate_management.mail_template_installment_overdue')
                        line._schedule_activity(self.env._("Overdue installment"))
                        line.overdue_notified = True
            except Exception:
                _logger.exception("Overdue processing failed for line %s", line.id)

    @api.model
    def _cron_send_receipts(self):
        """ Every few minutes: email a receipt for installments that were just paid. """
        for line in self.search([('state', '=', 'paid'), ('receipt_sent', '=', False)], limit=50):
            try:
                with self.env.cr.savepoint():
                    line._send_template('real_estate_management.mail_template_payment_receipt')
                    line.receipt_sent = True
                    line.booking_id.message_post(
                        body=self.env._("Payment received for %s.", line.name))
            except Exception:
                _logger.exception("Receipt failed for line %s", line.id)
