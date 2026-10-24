import { Component, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const LOCALE = "en-IN"; // lakh/crore grouping: 78,45,000
const TYPE_COLORS = { flat: "#3a6df0", plot: "#2e9e6b", shop: "#f08a24" };
const FALLBACK_COLORS = ["#714b67", "#e5484d", "#14a3b8", "#8a90a6"];
const PROJECT_COLORS = ["#3a6df0", "#2e9e6b", "#f08a24", "#714b67", "#14a3b8", "#8a90a6"];
const BOOKING_STATE_TONE = {
    draft: "muted", booked: "info", agreement: "success",
    installment: "purple", handover: "success", cancel: "danger",
};
const INSTALLMENT_STATUS = {
    overdue: { label: "Overdue", tone: "danger" },
    soon: { label: "Due Soon", tone: "warning" },
    upcoming: { label: "Upcoming", tone: "info" },
};
const ACTIVITY_STYLE = {
    payment: { icon: "fa-check", tone: "success" },
    booking: { icon: "fa-file-text-o", tone: "info" },
    commission: { icon: "fa-star", tone: "purple" },
    reminder: { icon: "fa-envelope-o", tone: "warning" },
};
const PRESETS = [
    { key: "this_month", label: "This Month" },
    { key: "last_month", label: "Last Month" },
    { key: "last_30", label: "Last 30 Days" },
    { key: "this_quarter", label: "This Quarter" },
    { key: "this_year", label: "This Year" },
    { key: "custom", label: "Custom Range" },
];

const EXPORT_FORMATS = [
    { key: "csv", label: "CSV", hint: "Comma separated values", icon: "fa-file-text-o" },
    { key: "pdf", label: "PDF", hint: "Printable report", icon: "fa-file-pdf-o" },
    { key: "xlsx", label: "Excel", hint: "Formatted workbook (.xlsx)", icon: "fa-file-excel-o" },
];
const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const PDF_REPORT = "real_estate_management.report_dashboard";

const pad = (n) => String(n).padStart(2, "0");
const toIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (s) => new Date(`${s}T00:00:00`);

function presetRange(key) {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    switch (key) {
        case "last_month":
            return [new Date(y, m - 1, 1), new Date(y, m, 0)];
        case "last_30":
            return [new Date(y, m, now.getDate() - 29), now];
        case "this_quarter":
            return [new Date(y, Math.floor(m / 3) * 3, 1), now];
        case "this_year":
            return [new Date(y, 0, 1), now];
        default:
            return [new Date(y, m, 1), now];
    }
}

function niceMax(value, min = 4) {
    const v = Math.max(value, min);
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    return [1, 2, 2.5, 5, 10].map((f) => f * pow).find((c) => c >= v);
}

function compact(value) {
    const abs = Math.abs(value);
    const fmt = (n, s) => `${parseFloat(n.toFixed(1))}${s}`;
    if (abs >= 1e7) return fmt(value / 1e7, "Cr");
    if (abs >= 1e5) return fmt(value / 1e5, "L");
    if (abs >= 1e3) return fmt(value / 1e3, "K");
    return String(parseFloat(value.toFixed(1)));
}

export class RealEstateDashboard extends Component {
    static template = "real_estate_management.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.presets = PRESETS;
        this.exportFormats = EXPORT_FORMATS;
        this.exportRef = useRef("exportRef");
        this.state = useState({
            loading: true,
            error: null,
            data: null,
            preset: "this_month",
            dateFrom: "",
            dateTo: "",
            exportOpen: false,
            exporting: null,
        });
        useExternalListener(document, "click", this.onDocumentClick);
        useExternalListener(document, "keydown", this.onDocumentKeydown);
        this.applyPreset("this_month");
        onWillStart(() => this.load());
    }

    
    // Data
    
    applyPreset(key) {
        const [from, to] = presetRange(key);
        this.state.preset = key;
        this.state.dateFrom = toIso(from);
        this.state.dateTo = toIso(to);
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call("real.estate.dashboard", "get_dashboard_data", [], {
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
            });
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || "Could not load the dashboard.";
        } finally {
            this.state.loading = false;
        }
    }

    onPresetChange(ev) {
        const key = ev.target.value;
        if (key === "custom") {
            this.state.preset = key;
            return;
        }
        this.applyPreset(key);
        this.load();
    }

    onDateChange(field, ev) {
        this.state[field] = ev.target.value;
        this.state.preset = "custom";
        if (this.state.dateFrom && this.state.dateTo) {
            this.load();
        }
    }

    
    // Formatting
    
    num(value) {
        return new Intl.NumberFormat(LOCALE).format(value || 0);
    }

    money(value) {
        const cur = this.state.data.currency;
        const text = new Intl.NumberFormat(LOCALE, {
            minimumFractionDigits: 0,
            maximumFractionDigits: cur.decimals > 0 ? 2 : 0,
        }).format(value || 0);
        return cur.position === "after" ? `${text} ${cur.symbol}` : `${cur.symbol} ${text}`;
    }

    shortDate(iso) {
        return parseIso(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    }

    timeAgo(ts) {
        const seconds = (Date.now() - new Date(ts.replace(" ", "T") + "Z").getTime()) / 1000;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 1) return "Just now";
        if (minutes < 60) return `${minutes} min ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours} hr ago`;
        const days = Math.floor(hours / 24);
        return `${days} day${days > 1 ? "s" : ""} ago`;
    }

    delta(change) {
        if (change === null || change === undefined) {
            return null;
        }
        return { up: change >= 0, text: `${Math.abs(change)}%` };
    }

    
    // View models
    
    get rangeLabel() {
        return `${this.shortDate(this.state.dateFrom)} - ${this.shortDate(this.state.dateTo)}`;
    }

    get kpis() {
        const k = this.state.data.kpis;
        const range = this.dateDomain("booking_date");
        return [
            {
                key: "units", tone: "purple", icon: "fa-home", label: "Total Units",
                value: this.num(k.units.total),
                sub: `${this.num(k.units.sold)} Sold · ${this.num(k.units.available)} Available`,
                delta: null,
                onClick: () => this.openList("Units", "real.estate.unit", []),
            },
            {
                key: "bookings", tone: "green", icon: "fa-calendar-check-o", label: "Total Bookings",
                value: this.num(k.bookings.count),
                sub: "This period",
                delta: this.delta(k.bookings.change),
                onClick: () => this.openList("Bookings", "real.estate.booking",
                    [...range, ["state", "not in", ["draft", "cancel"]]]),
            },
            {
                key: "collection", tone: "blue", icon: "fa-money", label: "Total Collection",
                value: this.money(k.collection.amount),
                sub: "This period",
                delta: this.delta(k.collection.change),
                onClick: () => this.openList("Collection", "real.estate.installment.line",
                    [["state", "=", "paid"], ...this.dateDomain("paid_date")]),
            },
            {
                key: "due", tone: "orange", icon: "fa-clock-o", label: "Due Installments",
                value: this.num(k.due.count),
                sub: `Overdue ${this.num(k.due.overdue)} · Upcoming ${this.num(k.due.upcoming)}`,
                delta: null,
                onClick: () => this.openList("Due Installments", "real.estate.installment.line",
                    [["state", "in", ["due", "overdue"]], ["booking_id.state", "in", ["booked", "agreement", "installment"]]]),
            },
            {
                key: "commissions", tone: "red", icon: "fa-users", label: "Pending Commissions",
                value: this.money(k.commissions.amount),
                sub: `To be approved ${this.num(k.commissions.count)}`,
                delta: null,
                onClick: () => this.openList("Pending Commissions", "real.estate.commission",
                    [["state", "=", "draft"]]),
            },
        ];
    }

    get trend() {
        const rows = this.state.data.trend;
        const W = 640, H = 250, pl = 34, pr = 56, pt = 14, pb = 28;
        const iw = W - pl - pr;
        const ih = H - pt - pb;
        const maxB = niceMax(Math.max(...rows.map((r) => r.bookings)));
        const maxC = niceMax(Math.max(...rows.map((r) => r.collection)), 1000);
        const x = (i) => pl + (rows.length > 1 ? (i * iw) / (rows.length - 1) : iw / 2);
        const y = (v, max) => pt + ih - (v / max) * ih;
        const bPts = rows.map((r, i) => [x(i), y(r.bookings, maxB)]);
        const cPts = rows.map((r, i) => [x(i), y(r.collection, maxC)]);
        const line = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
        const area = (pts) =>
            `${line(pts)} L${pts[pts.length - 1][0].toFixed(1)} ${pt + ih} L${pts[0][0].toFixed(1)} ${pt + ih} Z`;
        const ticks = [0, 1, 2, 3, 4].map((i) => ({
            y: pt + ih - (i / 4) * ih,
            left: compact((maxB * i) / 4),
            right: compact((maxC * i) / 4),
        }));
        return {
            W, H, pl, pr, ih, pt,
            ticks,
            bLine: line(bPts), bArea: area(bPts),
            cLine: line(cPts), cArea: area(cPts),
            labels: rows.map((r, i) => ({ x: x(i), text: r.label })),
            bDots: rows.map((r, i) => ({ x: bPts[i][0], y: bPts[i][1], tip: `${r.label}: ${r.bookings} bookings` })),
            cDots: rows.map((r, i) => ({ x: cPts[i][0], y: cPts[i][1], tip: `${r.label}: ${this.money(r.collection)}` })),
        };
    }

    get donut() {
        const R = 70;
        const C = 2 * Math.PI * R;
        const items = this.state.data.by_type;
        const total = items.reduce((s, i) => s + i.count, 0);
        let offset = 0;
        const segments = items.map((item, idx) => {
            const share = total ? item.count / total : 0;
            const len = share * C;
            const seg = {
                key: item.key,
                label: item.label,
                count: item.count,
                percent: Math.round(share * 100),
                color: TYPE_COLORS[item.key] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length],
                dash: `${Math.max(len - (items.length > 1 ? 2 : 0), 0)} ${C}`,
                offset: -offset,
            };
            offset += len;
            return seg;
        });
        return { R, total, segments };
    }

    get projects() {
        const rows = this.state.data.by_project;
        const max = Math.max(...rows.map((r) => r.count), 1);
        return rows.map((r, i) => ({
            ...r,
            width: Math.max((r.count / max) * 100, 3),
            color: PROJECT_COLORS[i % PROJECT_COLORS.length],
        }));
    }

    get bookings() {
        return this.state.data.recent_bookings.map((b) => ({
            ...b,
            tone: BOOKING_STATE_TONE[b.state] || "muted",
        }));
    }

    get installments() {
        return this.state.data.upcoming.map((l) => ({ ...l, ...INSTALLMENT_STATUS[l.status] }));
    }

    get activities() {
        return this.state.data.activities.map((a) => ({
            ...a,
            ...ACTIVITY_STYLE[a.kind],
            label: a.amount ? `${a.text} (${this.money(a.amount)})` : a.text,
        }));
    }

    
    // Navigation
    
    dateDomain(field) {
        return [[field, ">=", this.state.data.date_from], [field, "<=", this.state.data.date_to]];
    }

    openList(name, model, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    openBooking(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "real.estate.booking",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    viewAllBookings() {
        this.openList("Bookings", "real.estate.booking", [["state", "!=", "cancel"]]);
    }

    viewAllInstallments() {
        this.openList("Installments", "real.estate.installment.line",
            [["state", "in", ["due", "overdue"]], ["booking_id.state", "in", ["booked", "agreement", "installment"]]]);
    }

    viewBookingRequests() {
        this.openList("Booking Requests", "real.estate.booking.request", []);
    }

    
    // Export (CSV / PDF / Excel)
    
    toggleExport() {
        if (!this.state.data || this.state.exporting) {
            return;
        }
        this.state.exportOpen = !this.state.exportOpen;
    }

    onDocumentClick(ev) {
        if (this.state.exportOpen && !this.exportRef.el?.contains(ev.target)) {
            this.state.exportOpen = false;
        }
    }

    onDocumentKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.exportOpen = false;
        }
    }

    async exportReport(format) {
        if (!this.state.data || this.state.exporting) {
            return;
        }
        this.state.exportOpen = false;
        this.state.exporting = format;
        try {
            if (format === "pdf") {
                await this.exportPdf();
            } else if (format === "xlsx") {
                await this.exportXlsx();
            } else {
                this.exportCsv();
            }
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || "Could not export the report.",
                { type: "danger" }
            );
        } finally {
            this.state.exporting = null;
        }
    }

    downloadBlob(blob, filename) {
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    async exportPdf() {
        const d = this.state.data;
        await this.action.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: PDF_REPORT,
            report_file: PDF_REPORT,
            data: { date_from: d.date_from, date_to: d.date_to },
            context: {},
        });
    }

    async exportXlsx() {
        const d = this.state.data;
        const res = await this.orm.call("real.estate.dashboard", "export_xlsx", [], {
            date_from: d.date_from,
            date_to: d.date_to,
        });
        const binary = atob(res.content);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        this.downloadBlob(new Blob([bytes], { type: XLSX_MIME }), res.filename);
    }

    exportCsv() {
        const d = this.state.data;
        const k = d.kpis;
        const rows = [
            ["Real Estate Dashboard", this.rangeLabel],
            [],
            ["Summary", "Value"],
            ["Total Units", k.units.total],
            ["Sold Units", k.units.sold],
            ["Available Units", k.units.available],
            ["Bookings (period)", k.bookings.count],
            ["Collection (period)", k.collection.amount],
            ["Due Installments", k.due.count],
            ["Overdue Installments", k.due.overdue],
            ["Pending Commissions (count)", k.commissions.count],
            ["Pending Commissions (amount)", k.commissions.amount],
            [],
            ["Month", "Bookings", "Collection"],
            ...d.trend.map((t) => [t.label, t.bookings, t.collection]),
            [],
            ["Project", "Bookings"],
            ...d.by_project.map((p) => [p.label, p.count]),
            [],
            ["Booking", "Customer", "Unit", "Project", "Amount", "Status", "Date"],
            ...d.recent_bookings.map((b) => [b.name, b.customer, b.unit, b.project, b.amount, b.state_label, b.date]),
            [],
            ["Due Date", "Customer", "Amount", "Status"],
            ...this.installments.map((l) => [l.due_date, l.customer, l.amount, l.label]),
        ];
        const csv = rows
            .map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(","))
            .join("\r\n");
        this.downloadBlob(
            new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }),
            `real_estate_dashboard_${d.date_from}_${d.date_to}.csv`
        );
    }
}

registry.category("actions").add("real_estate_management.dashboard", RealEstateDashboard);