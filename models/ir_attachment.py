from odoo import fields, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    re_cloud_url = fields.Char("Cloudinary URL", copy=False)
    re_cloud_public_id = fields.Char("Cloudinary Public ID", copy=False, index='btree_not_null')
