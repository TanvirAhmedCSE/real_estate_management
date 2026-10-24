from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class RealEstatePortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'booking_count' in counters:
            Booking = request.env['real.estate.booking']
            values['booking_count'] = (
                Booking.search_count(self._booking_domain()) if Booking.has_access('read') else 0)
        return values

    def _booking_domain(self):
        return [('state', 'not in', ('draft',))]

    def _get_booking(self, booking_id):
        """ Record rules restrict this search to the customer's own bookings. """
        booking = request.env['real.estate.booking'].search(
            [('id', '=', booking_id)] + self._booking_domain(), limit=1)
        return booking.sudo() if booking else False

    @http.route('/my/bookings', type='http', auth='user', website=True)
    def portal_my_bookings(self, **kw):
        bookings = request.env['real.estate.booking'].search(
            self._booking_domain(), order='booking_date desc')
        return request.render('real_estate_management.portal_my_bookings', {
            'bookings': bookings.sudo(),
            'page_name': 'booking',
            'default_url': '/my/bookings',
        })

    @http.route('/my/bookings/<int:booking_id>', type='http', auth='user', website=True)
    def portal_my_booking(self, booking_id, **kw):
        booking = self._get_booking(booking_id)
        if not booking:
            return request.redirect('/my/bookings')
        return request.render('real_estate_management.portal_my_booking', {
            'booking': booking,
            'page_name': 'booking',
        })

    @http.route('/my/bookings/<int:booking_id>/statement', type='http', auth='user', website=True)
    def portal_booking_statement(self, booking_id, **kw):
        booking = self._get_booking(booking_id)
        if not booking:
            return request.redirect('/my/bookings')
        return self._show_report(
            model=booking, report_type='pdf',
            report_ref='real_estate_management.action_report_statement', download=True)

    @http.route('/my/installments/<int:line_id>/pay', type='http', auth='user', website=True)
    def portal_installment_pay(self, line_id, **kw):
        line = request.env['real.estate.installment.line'].search([('id', '=', line_id)], limit=1)
        if not line or line.state not in ('due', 'overdue'):
            return request.redirect('/my/bookings')
        line_sudo = line.sudo()
        line_sudo._ensure_invoice()
        return request.redirect(line_sudo.invoice_id.get_portal_url())

    @http.route('/my/installments/<int:line_id>/receipt', type='http', auth='user', website=True)
    def portal_installment_receipt(self, line_id, **kw):
        line = request.env['real.estate.installment.line'].search(
            [('id', '=', line_id), ('state', '=', 'paid')], limit=1)
        if not line:
            return request.redirect('/my/bookings')
        return self._show_report(
            model=line.sudo(), report_type='pdf',
            report_ref='real_estate_management.action_report_receipt', download=True)
