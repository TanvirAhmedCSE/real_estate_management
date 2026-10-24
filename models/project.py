from odoo import api, fields, models


class RealEstateProject(models.Model):
    _name = 'real.estate.project'
    _description = 'Real Estate Project'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char()
    location = fields.Char(tracking=True)
    description = fields.Text()
    state = fields.Selection(
        [('planning', "Planning"), ('ongoing', "Ongoing"), ('completed', "Completed")],
        default='ongoing', required=True, tracking=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    building_ids = fields.One2many('real.estate.building', 'project_id')
    unit_ids = fields.One2many('real.estate.unit', 'project_id')
    building_count = fields.Integer(compute='_compute_counts')
    unit_count = fields.Integer(compute='_compute_counts')
    available_count = fields.Integer(compute='_compute_counts')
    sold_count = fields.Integer(compute='_compute_counts')

    @api.depends('building_ids', 'unit_ids.state')
    def _compute_counts(self):
        for project in self:
            units = project.unit_ids
            project.building_count = len(project.building_ids)
            project.unit_count = len(units)
            project.available_count = len(units.filtered(lambda u: u.state == 'available'))
            project.sold_count = len(units.filtered(lambda u: u.state == 'sold'))

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Units"),
            'res_model': 'real.estate.unit',
            'view_mode': 'kanban,list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }


class RealEstateBuilding(models.Model):
    _name = 'real.estate.building'
    _description = 'Building / Block'
    _order = 'project_id, name'

    name = fields.Char("Building / Block", required=True)
    project_id = fields.Many2one('real.estate.project', required=True, ondelete='cascade', index=True)
    floors = fields.Integer("Total Floors")
    unit_ids = fields.One2many('real.estate.unit', 'building_id')
    unit_count = fields.Integer(compute='_compute_unit_count')

    @api.depends('unit_ids')
    def _compute_unit_count(self):
        for building in self:
            building.unit_count = len(building.unit_ids)

    @api.depends('name', 'project_id.name')
    def _compute_display_name(self):
        for building in self:
            building.display_name = ' / '.join(filter(None, [building.project_id.name, building.name]))
