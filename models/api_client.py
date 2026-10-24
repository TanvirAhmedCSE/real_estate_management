import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM = 'real_estate_management.'
TIMEOUT = 45  


class RealEstateApi(models.AbstractModel):
    _name = 'real.estate.api'
    _description = 'App Server API Client'

    @api.model
    def _get_config(self):
        icp = self.env['ir.config_parameter'].sudo()
        url = (icp.get_param(PARAM + 'api_url') or '').strip().rstrip('/')
        secret = (icp.get_param(PARAM + 'api_secret') or '').strip()
        return url, secret

    @api.model
    def _is_configured(self):
        return all(self._get_config())

    @api.model
    def _call(self, method, path, payload=None):
        url, secret = self._get_config()
        if not (url and secret):
            raise UserError(self.env._("Set the App API URL and secret in Real Estate settings first."))
        try:
            response = requests.request(
                method, url + path, json=payload, headers={'x-sync-secret': secret}, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            raise UserError(self.env._("App server request failed: %s", str(e))) from e