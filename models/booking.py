from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class RealEstateBooking(models.Model):
    _name = 'real.estate.booking'
    _description = 'Property Booking'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'booking_date desc, id desc'

    def _default_agent(self):
        partner = self.env.user.partner_id
        return partner.id if partner.is_agent else False

    name = fields.Char("Booking No.", default='New', readonly=True, copy=False)
    state = fields.Selection([
        ('draft', "Lead / Draft"),
        ('booked', "Booked"),
        ('agreement', "Agreement"),
        ('installment', "Installment Running"),
        ('handover', "Handed Over"),
        ('cancel', "Cancelled"),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    partner_id = fields.Many2one('res.partner', "Customer", required=True, tracking=True, index=True)
    agent_id = fields.Many2one('res.partner', "Sales Agent", domain=[('is_agent', '=', True)],
                               default=_default_agent, tracking=True, index=True)
    user_id = fields.Many2one('res.users', "Responsible", default=lambda self: self.env.user, tracking=True)
    unit_id = fields.Many2one('real.estate.unit', "Unit", required=True, tracking=True, index=True,
                              domain="[('state', '=', 'available')]")
    project_id = fields.Many2one(related='unit_id.project_id', store=True, index=True)
    building_id = fields.Many2one(related='unit_id.building_id', store=True)
    booking_date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)

    # Pricing & plan (merged installment plan)
    unit_price = fields.Monetary(compute='_compute_unit_price', store=True, readonly=False, tracking=True)
    discount = fields.Monetary(tracking=True)
    total_price = fields.Monetary(compute='_compute_total_price', store=True)
    booking_money = fields.Monetary(tracking=True)
    down_payment = fields.Monetary(tracking=True)
    down_payment_date = fields.Date()
    handover_amount = fields.Monetary("Handover Payment", tracking=True)
    handover_due_date = fields.Date()
    installment_count = fields.Integer("No. of Installments", default=12)
    interval_months = fields.Integer("Interval (months)", default=1)
    first_installment_date = fields.Date()
    remaining_amount = fields.Monetary("To Be Paid in Installments", compute='_compute_plan_amounts')
    installment_amount = fields.Monetary("Installment Amount", compute='_compute_plan_amounts')

    agreement_date = fields.Date(readonly=True, copy=False)
    handover_date = fields.Date(readonly=True, copy=False)
    cancel_date = fields.Date(readonly=True, copy=False)
    cancel_reason = fields.Text(readonly=True, copy=False)
    refund_amount = fields.Monetary(readonly=True, copy=False)
    note = fields.Text()

    line_ids = fields.One2many('real.estate.installment.line', 'booking_id', string="Installments", copy=False)
    commission_ids = fields.One2many('real.estate.commission', 'booking_id', copy=False)
    line_count = fields.Integer(compute='_compute_counts')
    invoice_count = fields.Integer(compute='_compute_counts')
    paid_amount = fields.Monetary(compute='_compute_amounts')
    balance_amount = fields.Monetary(compute='_compute_amounts')
    overdue_amount = fields.Monetary(compute='_compute_amounts')
    overdue_count = fields.Integer(compute='_compute_amounts')
    progress = fields.Float("Collected (%)", compute='_compute_amounts')

    
    # Computes / constraints
    
    @api.depends('unit_id')
    def _compute_unit_price(self):
        for booking in self:
            if booking.unit_id:
                booking.unit_price = booking.unit_id.price

    @api.depends('unit_price', 'discount')
    def _compute_total_price(self):
        for booking in self:
            booking.total_price = booking.unit_price - booking.discount

    @api.depends('total_price', 'booking_money', 'down_payment', 'handover_amount', 'installment_count')
    def _compute_plan_amounts(self):
        for booking in self:
            remaining = (booking.total_price - booking.booking_money
                         - booking.down_payment - booking.handover_amount)
            booking.remaining_amount = remaining
            booking.installment_amount = (
                booking.currency_id.round(remaining / booking.installment_count)
                if booking.installment_count > 0 else 0.0)

    @api.depends('line_ids.state', 'line_ids.amount', 'line_ids.late_fee', 'total_price')
    def _compute_amounts(self):
        for booking in self:
            lines = booking.line_ids.filtered(lambda l: l.state != 'cancel')
            paid = sum(lines.filtered(lambda l: l.state == 'paid').mapped('amount'))
            overdue = lines.filtered(lambda l: l.state == 'overdue')
            booking.paid_amount = paid
            booking.balance_amount = booking.total_price - paid if booking.state != 'cancel' else 0.0
            booking.overdue_amount = sum(overdue.mapped('total_amount'))
            booking.overdue_count = len(overdue)
            booking.progress = (paid / booking.total_price * 100.0) if booking.total_price else 0.0

    def _compute_counts(self):
        Move = self.env['account.move'].sudo()
        for booking in self:
            booking.line_count = len(booking.line_ids)
            booking.invoice_count = Move.search_count([('re_booking_id', '=', booking.id)])

    @api.constrains('discount', 'unit_price', 'booking_money', 'down_payment', 'handover_amount',
                    'total_price', 'installment_count', 'interval_months')
    def _check_plan(self):
        for booking in self:
            if min(booking.discount, booking.booking_money, booking.down_payment,
                   booking.handover_amount) < 0:
                raise ValidationError(self.env._("Amounts cannot be negative."))
            if booking.discount > booking.unit_price:
                raise ValidationError(self.env._("Discount cannot exceed the unit price."))
            if (booking.booking_money + booking.down_payment + booking.handover_amount
                    > booking.total_price):
                raise ValidationError(self.env._(
                    "Booking money + down payment + handover payment cannot exceed the total price."))
            if booking.installment_count < 0 or booking.interval_months < 1:
                raise ValidationError(self.env._("Invalid installment count or interval."))

    
    # ORM
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('real.estate.booking') or 'New'
        return super().create(vals_list)

    def unlink(self):
        if any(b.state not in ('draft', 'cancel') for b in self):
            raise UserError(self.env._("Only draft or cancelled bookings can be deleted."))
        return super().unlink()

    
    # Helpers
    
    def _create_line(self, line_type, name, amount, due_date, sequence):
        self.ensure_one()
        return self.env['real.estate.installment.line'].sudo().create({
            'booking_id': self.id,
            'line_type': line_type,
            'name': name,
            'amount': amount,
            'due_date': due_date,
            'sequence': sequence,
        })

    def _generate_installment_lines(self, amount, count, interval, first_date, label=None):
        """ Split `amount` in `count` monthly-interval installments (last one absorbs rounding). """
        self.ensure_one()
        label = label or self.env._("Installment")
        currency = self.currency_id
        base = currency.round(amount / count)
        seq = max(self.line_ids.mapped('sequence') or [0])
        allocated = 0.0
        vals_list = []
        for i in range(count):
            amt = base if i < count - 1 else currency.round(amount - allocated)
            allocated += amt
            vals_list.append({
                'booking_id': self.id,
                'line_type': 'installment',
                'name': "%s %d/%d" % (label, i + 1, count),
                'amount': amt,
                'due_date': first_date + relativedelta(months=interval * i),
                'sequence': seq + 10 * (i + 1),
            })
        return self.env['real.estate.installment.line'].sudo().create(vals_list)

    
    # Workflow
    
    def action_confirm(self):
        for booking in self:
            if booking.state != 'draft':
                continue
            if booking.unit_id.state != 'available':
                raise UserError(self.env._("Unit %s is not available.", booking.unit_id.display_name))
            if booking.total_price <= 0:
                raise UserError(self.env._("The total price must be greater than zero."))
            if booking.booking_money <= 0:
                raise UserError(self.env._("Please enter the booking money."))
            booking.unit_id.sudo().write({'state': 'booked'})
            booking.state = 'booked'
            line = booking._create_line(
                'booking', self.env._("Booking Money"), booking.booking_money, booking.booking_date, 10)
            line._ensure_invoice()
            booking.message_post(body=self.env._("Booking confirmed. Booking money invoice created."))
        return True

    def action_agreement(self):
        for booking in self:
            if booking.state != 'booked':
                continue
            money = booking.line_ids.filtered(lambda l: l.line_type == 'booking' and l.state != 'cancel')
            if not money or any(l.state != 'paid' for l in money):
                raise UserError(self.env._("The booking money must be paid before the agreement."))
            booking.write({'state': 'agreement', 'agreement_date': fields.Date.context_today(booking)})
            booking.unit_id.sudo().write({'state': 'sold'})
            if booking.agent_id and not booking.commission_ids:
                self.env['real.estate.commission'].sudo().create({
                    'booking_id': booking.id,
                    'agent_id': booking.agent_id.id,
                    'rate': booking.agent_id.commission_rate,
                })
        return True

    def action_generate_plan(self):
        for booking in self:
            if booking.state != 'agreement':
                raise UserError(self.env._("The installment plan can only be generated after the agreement."))
            if booking.line_ids.filtered(lambda l: l.line_type != 'booking'):
                raise UserError(self.env._("The installment plan has already been generated."))
            remaining = booking.remaining_amount
            if remaining > 0 and booking.installment_count < 1:
                raise UserError(self.env._("Please set the number of installments."))
            base_date = booking.agreement_date or booking.booking_date
            interval = booking.interval_months
            down_date = booking.down_payment_date or base_date + relativedelta(months=1)
            first = booking.first_installment_date or down_date + relativedelta(months=interval)
            if booking.down_payment:
                booking._create_line('down', self.env._("Down Payment"), booking.down_payment, down_date, 20)
            last_due = down_date
            if remaining > 0:
                lines = booking._generate_installment_lines(remaining, booking.installment_count, interval, first)
                last_due = max(lines.mapped('due_date'))
            handover_due = booking.handover_due_date or last_due + relativedelta(months=interval)
            if booking.handover_amount:
                booking._create_line('handover', self.env._("Handover Payment"),
                                     booking.handover_amount, handover_due, 10000)
            booking.write({
                'state': 'installment',
                'down_payment_date': down_date,
                'first_installment_date': first,
                'handover_due_date': handover_due,
            })
            booking.message_post(body=self.env._("Installment plan generated."))
        return True

    def action_handover(self):
        for booking in self:
            if booking.state != 'installment':
                continue
            if booking.line_ids.filtered(lambda l: l.state in ('due', 'overdue')):
                raise UserError(self.env._("All installments must be paid before handover."))
            booking.write({'state': 'handover', 'handover_date': fields.Date.context_today(booking)})
        return True

    def action_cancel_draft(self):
        self.filtered(lambda b: b.state == 'draft').write({'state': 'cancel'})
        return True

    def action_open_reschedule(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Reschedule Installments"),
            'res_model': 'real.estate.reschedule.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_booking_id': self.id},
        }

    def action_open_cancel(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Cancel Booking"),
            'res_model': 'real.estate.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_booking_id': self.id},
        }

    
    # Smart buttons / reports
    
    def _get_report_base_filename(self):
        self.ensure_one()
        return "Statement - %s" % self.name
    
    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Invoices"),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('re_booking_id', '=', self.id)],
            'context': {'default_move_type': 'out_invoice'},
        }

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Installments"),
            'res_model': 'real.estate.installment.line',
            'view_mode': 'list,form',
            'domain': [('booking_id', '=', self.id)],
        }
