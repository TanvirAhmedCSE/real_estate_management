from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestInstallment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env['res.partner'].create({
            'name': 'Agent', 'is_agent': True, 'commission_rate': 2.0})
        cls.customer = cls.env['res.partner'].create({'name': 'Buyer', 'email': 'buyer@example.com'})
        project = cls.env['real.estate.project'].create({'name': 'P1'})
        cls.unit = cls.env['real.estate.unit'].create({
            'name': 'U1', 'project_id': project.id, 'price': 1000000.0})
        cls.booking = cls.env['real.estate.booking'].create({
            'partner_id': cls.customer.id,
            'agent_id': cls.agent.id,
            'unit_id': cls.unit.id,
            'booking_money': 100000.0,
            'down_payment': 200000.0,
            'handover_amount': 100000.0,
            'installment_count': 6,
        })

    def _pay(self, line):
        line._ensure_invoice()
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=line.invoice_id.ids).create({})
        payment._create_payments()

    def test_plan_generation(self):
        self.booking.action_confirm()
        self.assertEqual(self.unit.state, 'booked')
        self._pay(self.booking.line_ids)
        self.assertEqual(self.booking.line_ids.state, 'paid')
        self.booking.action_agreement()
        self.assertEqual(self.unit.state, 'sold')
        self.assertEqual(self.booking.commission_ids.amount, 20000.0)
        self.booking.action_generate_plan()
        lines = self.booking.line_ids
        installments = lines.filtered(lambda l: l.line_type == 'installment')
        self.assertEqual(len(installments), 6)
        self.assertAlmostEqual(sum(installments.mapped('amount')), 600000.0)
        self.assertAlmostEqual(sum(lines.mapped('amount')), 1000000.0)

    def test_late_fee_and_cancel(self):
        self.booking.action_confirm()
        self._pay(self.booking.line_ids)
        self.booking.action_agreement()
        self.booking.action_generate_plan()
        line = self.booking.line_ids.filtered(lambda l: l.line_type == 'installment')[0]
        line.due_date = fields.Date.today() - timedelta(days=5)
        self.env['real.estate.installment.line']._cron_installment_reminders()
        self.assertEqual(line.state, 'overdue')
        self.assertAlmostEqual(line.late_fee, line.amount * 0.02, places=2)
        self.assertAlmostEqual(line.invoice_id.amount_total, line.amount + line.late_fee, places=2)

        wizard = self.env['real.estate.cancel.wizard'].create({
            'booking_id': self.booking.id, 'reason': 'test'})
        wizard.action_cancel_booking()
        self.assertEqual(self.booking.state, 'cancel')
        self.assertEqual(self.unit.state, 'available')
