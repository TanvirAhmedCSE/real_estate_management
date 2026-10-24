import base64
import io
from datetime import timedelta

import xlsxwriter
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_date

MANAGER_GROUP = 'real_estate_management.group_real_estate_manager'
ACTIVE_BOOKING_STATES = ('booked', 'agreement', 'installment')
INSTALLMENT_STATUS_LABELS = {'overdue': 'Overdue', 'soon': 'Due Soon', 'upcoming': 'Upcoming'}
INR_FORMAT = r'[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00'


def indian_number(value, decimals=0):
    """ 7845000 -> '78,45,000' (lakh/crore grouping, same as the dashboard). """
    text = f"{abs(value or 0):.{decimals}f}"
    whole, _dot, frac = text.partition('.')
    if frac and not frac.strip('0'):
        frac = ''
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts + [tail])
    return f"{'-' if (value or 0) < 0 else ''}{whole}{'.' + frac if frac else ''}"


class RealEstateDashboard(models.AbstractModel):
    _name = 'real.estate.dashboard'
    _description = 'Real Estate Dashboard'

    
    # Helpers
    
    @api.model
    def _collected(self, domain, date_from, date_to):
        rows = self.env['real.estate.installment.line']._read_group(
            domain + [('state', '=', 'paid'), ('paid_date', '>=', date_from), ('paid_date', '<=', date_to)],
            aggregates=['paid_amount:sum'])
        return rows[0][0] or 0.0

    @staticmethod
    def _change(current, previous):
        """ % change vs the previous period (None when there is nothing to compare with). """
        if not previous:
            return None
        return round((current - previous) / previous * 100.0, 1)

    
    # Main entry point
    
    @api.model
    def get_dashboard_data(self, date_from=None, date_to=None):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._("Only Real Estate managers can open the dashboard."))

        env = self.env
        today = fields.Date.context_today(self)
        date_to = fields.Date.to_date(date_to) or today
        date_from = fields.Date.to_date(date_from) or date_to.replace(day=1)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        span = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=span - 1)

        Unit = env['real.estate.unit']
        Booking = env['real.estate.booking']
        Line = env['real.estate.installment.line']
        Commission = env['real.estate.commission']

        companies = env.companies.ids
        unit_dom = [('company_id', 'in', companies)]
        booking_dom = [('company_id', 'in', companies)]
        valid_booking = booking_dom + [('state', 'not in', ('draft', 'cancel'))]
        line_dom = [('company_id', 'in', companies)]
        open_line = line_dom + [('booking_id.state', 'in', ACTIVE_BOOKING_STATES)]
        commission_dom = [('company_id', 'in', companies)]

        currency = env.company.currency_id
        icp = env['ir.config_parameter'].sudo()
        reminder_days = int(icp.get_param('real_estate_management.reminder_days', 3))

        #  KPI: units 
        unit_states = dict(Unit._read_group(unit_dom, ['state'], ['__count']))
        units = {
            'total': sum(unit_states.values()),
            'sold': unit_states.get('sold', 0),
            'booked': unit_states.get('booked', 0),
            'available': unit_states.get('available', 0),
        }

        #  KPI: bookings & collection 
        def period_bookings(d1, d2):
            return Booking.search_count(
                valid_booking + [('booking_date', '>=', d1), ('booking_date', '<=', d2)])

        bookings_now = period_bookings(date_from, date_to)
        bookings_prev = period_bookings(prev_from, prev_to)
        collected = self._collected(line_dom, date_from, date_to)
        collected_prev = self._collected(line_dom, prev_from, prev_to)

        #  KPI: due installments / pending commissions 
        due_states = dict(Line._read_group(
            open_line + [('state', 'in', ('due', 'overdue'))], ['state'], ['__count']))
        commission_count, commission_amount = Commission._read_group(
            commission_dom + [('state', '=', 'draft')], aggregates=['__count', 'amount:sum'])[0]

        #  Trend (6 months ending with the month of date_to) 
        first_month = date_to.replace(day=1) - relativedelta(months=5)
        last_day = date_to + relativedelta(day=31)
        months = [first_month + relativedelta(months=i) for i in range(6)]
        booking_by_month = {
            (d.year, d.month): c for d, c in Booking._read_group(
                valid_booking + [('booking_date', '>=', first_month), ('booking_date', '<=', last_day)],
                ['booking_date:month'], ['__count'])}
        collection_by_month = {
            (d.year, d.month): s for d, s in Line._read_group(
                line_dom + [('state', '=', 'paid'), ('paid_date', '>=', first_month),
                            ('paid_date', '<=', last_day)],
                ['paid_date:month'], ['paid_amount:sum'])}
        trend = [{
            'label': format_date(env, m, date_format='MMM'),
            'bookings': booking_by_month.get((m.year, m.month), 0),
            'collection': collection_by_month.get((m.year, m.month), 0.0),
        } for m in months]

        #  Units by type 
        type_labels = dict(Unit._fields['unit_type']._description_selection(env))
        by_type = [{'key': t, 'label': type_labels.get(t, t), 'count': c}
                   for t, c in Unit._read_group(unit_dom, ['unit_type'], ['__count'], order='__count desc')]

        #  Sales by project (all time, top 5 + other) 
        rows = Booking._read_group(valid_booking, ['project_id'], ['__count'], order='__count desc')
        by_project = [{'label': p.name, 'count': c} for p, c in rows[:5]]
        other = sum(c for _p, c in rows[5:])
        if other:
            by_project.append({'label': env._("Other"), 'count': other})

        #  Recent bookings 
        state_labels = dict(Booking._fields['state']._description_selection(env))
        recent_bookings = [{
            'id': b.id,
            'name': b.name,
            'customer': b.partner_id.name,
            'unit': b.unit_id.name,
            'project': b.project_id.name or '',
            'amount': b.total_price,
            'state': b.state,
            'state_label': state_labels.get(b.state, b.state),
            'date': fields.Date.to_string(b.booking_date),
        } for b in Booking.search(
            booking_dom + [('state', '!=', 'cancel')], order='booking_date desc, id desc', limit=5)]

        #  Upcoming & overdue installments 
        soon_limit = today + timedelta(days=reminder_days)
        upcoming = []
        for line in Line.search(open_line + [('state', 'in', ('due', 'overdue'))],
                                order='due_date asc, id', limit=6):
            if line.state == 'overdue':
                status = 'overdue'
            elif line.due_date <= soon_limit:
                status = 'soon'
            else:
                status = 'upcoming'
            upcoming.append({
                'id': line.id,
                'booking_id': line.booking_id.id,
                'due_date': fields.Date.to_string(line.due_date),
                'customer': line.partner_id.name,
                'amount': line.total_amount,
                'status': status,
            })

        #  Recent activities 
        activities = []
        for line in Line.search(line_dom + [('state', '=', 'paid')], order='write_date desc', limit=4):
            activities.append({
                'kind': 'payment', 'ts': line.write_date, 'amount': line.paid_amount,
                'text': env._("Payment received from %s", line.partner_id.name)})
        for booking in Booking.search(booking_dom, order='create_date desc', limit=4):
            activities.append({
                'kind': 'booking', 'ts': booking.create_date, 'amount': 0.0,
                'text': env._("New booking %s created", booking.name)})
        for comm in Commission.search(
                commission_dom + [('state', '=', 'approved')], order='write_date desc', limit=3):
            activities.append({
                'kind': 'commission', 'ts': comm.write_date, 'amount': comm.amount,
                'text': env._("Commission approved for agent %s", comm.agent_id.name)})
        for line in Line.search(line_dom + [('reminder_sent', '=', True)], order='write_date desc', limit=3):
            activities.append({
                'kind': 'reminder', 'ts': line.write_date, 'amount': 0.0,
                'text': env._("Reminder sent to %s", line.partner_id.name)})
        activities.sort(key=lambda a: a['ts'], reverse=True)
        activities = [dict(a, ts=fields.Datetime.to_string(a['ts'])) for a in activities[:6]]

        #  Collection overview 
        pending = sum(Line.search(open_line + [
            ('state', 'in', ('due', 'overdue')),
            ('due_date', '>=', date_from), ('due_date', '<=', date_to),
        ]).mapped('total_amount'))
        total_expected = collected + pending

        return {
            'currency': {
                'id': currency.id, 'symbol': currency.symbol, 'position': currency.position,
                'decimals': currency.decimal_places,
            },
            'date_from': fields.Date.to_string(date_from),
            'date_to': fields.Date.to_string(date_to),
            'kpis': {
                'units': units,
                'bookings': {'count': bookings_now, 'change': self._change(bookings_now, bookings_prev),
                             'diff': bookings_now - bookings_prev},
                'collection': {'amount': collected, 'change': self._change(collected, collected_prev)},
                'due': {'count': sum(due_states.values()), 'overdue': due_states.get('overdue', 0),
                        'upcoming': due_states.get('due', 0)},
                'commissions': {'count': commission_count, 'amount': commission_amount or 0.0},
            },
            'trend': trend,
            'by_type': by_type,
            'by_project': by_project,
            'recent_bookings': recent_bookings,
            'upcoming': upcoming,
            'activities': activities,
            'overview': {
                'collected': collected,
                'pending': pending,
                'collected_pct': round(collected / total_expected * 100.0) if total_expected else 0,
                'pending_pct': round(pending / total_expected * 100.0) if total_expected else 0,
                'change': self._change(collected, collected_prev),
            },
        }

    
    # Excel export
    
    @api.model
    def export_xlsx(self, date_from=None, date_to=None):
        d = self.get_dashboard_data(date_from, date_to)
        k = d['kpis']
        period = f"{d['date_from']}  to  {d['date_to']}"

        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {'in_memory': True})
        f_title = wb.add_format({'bold': True, 'font_size': 15, 'font_color': '#714b67'})
        f_sub = wb.add_format({'font_color': '#6e7389'})
        f_head = wb.add_format({'bold': True, 'bg_color': '#714b67', 'font_color': '#ffffff', 'border': 1})
        f_text = wb.add_format({'border': 1})
        f_num = wb.add_format({'border': 1, 'num_format': '#,##0'})
        f_money = wb.add_format({'border': 1, 'num_format': INR_FORMAT})
        fmts = {'text': f_text, 'num': f_num, 'money': f_money}

        def sheet(name, headers, rows, kinds, widths, title=None):
            ws = wb.add_worksheet(name)
            ws.write(0, 0, title or name, f_title)
            ws.write(1, 0, period, f_sub)
            for c, h in enumerate(headers):
                ws.write(3, c, h, f_head)
                ws.set_column(c, c, widths[c])
            for r, row in enumerate(rows, start=4):
                for c, val in enumerate(row):
                    ws.write(r, c, val, fmts[kinds[c]])
            ws.freeze_panes(4, 0)
            return ws

        ws = sheet('Summary', ['Metric', 'Value'], [], ['text', 'num'], [34, 20], title='Real Estate Dashboard')
        summary_rows = [
            ('Total Units', k['units']['total'], f_num),
            ('Sold Units', k['units']['sold'], f_num),
            ('Booked Units', k['units']['booked'], f_num),
            ('Available Units', k['units']['available'], f_num),
            ('Bookings (period)', k['bookings']['count'], f_num),
            ('Collection (period)', k['collection']['amount'], f_money),
            ('Due Installments', k['due']['count'], f_num),
            ('Overdue Installments', k['due']['overdue'], f_num),
            ('Upcoming Installments', k['due']['upcoming'], f_num),
            ('Pending Commissions (count)', k['commissions']['count'], f_num),
            ('Pending Commissions (amount)', k['commissions']['amount'], f_money),
            ('Collected (overview)', d['overview']['collected'], f_money),
            ('Pending (overview)', d['overview']['pending'], f_money),
        ]
        for r, (label, value, fmt) in enumerate(summary_rows, start=4):
            ws.write(r, 0, label, f_text)
            ws.write(r, 1, value, fmt)

        sheet('Trend', ['Month', 'Bookings', 'Collection'],
              [[t['label'], t['bookings'], t['collection']] for t in d['trend']],
              ['text', 'num', 'money'], [14, 14, 20], title='Bookings & Collection Trend')
        sheet('Projects', ['Project', 'Bookings'],
              [[p['label'], p['count']] for p in d['by_project']],
              ['text', 'num'], [34, 14], title='Sales by Project')
        sheet('Recent Bookings',
              ['Booking', 'Customer', 'Unit', 'Project', 'Amount', 'Status', 'Date'],
              [[b['name'], b['customer'], b['unit'], b['project'], b['amount'], b['state_label'], b['date']]
               for b in d['recent_bookings']],
              ['text', 'text', 'text', 'text', 'money', 'text', 'text'],
              [16, 24, 12, 26, 18, 18, 14], title='Recent Bookings')
        sheet('Installments', ['Due Date', 'Customer', 'Amount', 'Status'],
              [[u['due_date'], u['customer'], u['amount'], INSTALLMENT_STATUS_LABELS.get(u['status'], u['status'])]
               for u in d['upcoming']],
              ['text', 'text', 'money', 'text'], [14, 26, 18, 14], title='Upcoming & Overdue Installments')
        wb.close()

        return {
            'filename': f"real_estate_dashboard_{d['date_from']}_{d['date_to']}.xlsx",
            'content': base64.b64encode(out.getvalue()).decode(),
        }


class ReportRealEstateDashboard(models.AbstractModel):
    """ Data provider for the dashboard PDF (QWeb report real_estate_management.report_dashboard). """
    _name = 'report.real_estate_management.report_dashboard'
    _description = 'Real Estate Dashboard PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        d = self.env['real.estate.dashboard'].get_dashboard_data(data.get('date_from'), data.get('date_to'))
        code = self.env.company.currency_id.name

        def money(value):
            return f"{code} {indian_number(value, d['currency']['decimals'])}"

        k = d['kpis']
        change = k['collection']['change']
        summary = [
            ('Total Units', indian_number(k['units']['total'])),
            ('Sold / Booked / Available', "%s / %s / %s" % (
                indian_number(k['units']['sold']), indian_number(k['units']['booked']),
                indian_number(k['units']['available']))),
            ('Bookings (period)', indian_number(k['bookings']['count'])),
            ('Collection (period)', money(k['collection']['amount'])),
            ('Due Installments', "%s (Overdue %s, Upcoming %s)" % (
                indian_number(k['due']['count']), indian_number(k['due']['overdue']),
                indian_number(k['due']['upcoming']))),
            ('Pending Commissions', "%s (%s)" % (money(k['commissions']['amount']),
                                                 indian_number(k['commissions']['count']))),
            ('Collected vs Pending', "%s (%s%%) / %s (%s%%)" % (
                money(d['overview']['collected']), d['overview']['collected_pct'],
                money(d['overview']['pending']), d['overview']['pending_pct'])),
        ]
        return {
            'doc_ids': [],
            'doc_model': 'real.estate.dashboard',
            'docs': [],
            'company': self.env.company,
            'd': d,
            'period': f"{d['date_from']}  to  {d['date_to']}",
            'summary': summary,
            'trend': [dict(t, collection_txt=money(t['collection'])) for t in d['trend']],
            'bookings': [dict(b, amount_txt=money(b['amount'])) for b in d['recent_bookings']],
            'installments': [dict(u, amount_txt=money(u['amount']),
                                  label=INSTALLMENT_STATUS_LABELS.get(u['status'], u['status']))
                             for u in d['upcoming']],
        }