import hashlib
import logging
import time

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM = 'real_estate_management.'
TIMEOUT = 60
ENDPOINT = 'https://api.cloudinary.com/v1_1/%s/image/%s'


class RealEstateCloudinary(models.AbstractModel):
    _name = 'real.estate.cloudinary'
    _description = 'Cloudinary Image Storage Client'

    @api.model
    def _get_config(self):
        icp = self.env['ir.config_parameter'].sudo()
        return tuple(
            (icp.get_param(PARAM + key) or '').strip()
            for key in ('cloudinary_cloud_name', 'cloudinary_api_key', 'cloudinary_api_secret'))

    @api.model
    def _is_configured(self):
        return all(self._get_config())

    @staticmethod
    def _sign(params, secret):
        """ Cloudinary signature: sorted `k=v` pairs joined by '&', secret appended, SHA-1. """
        to_sign = '&'.join('%s=%s' % (key, params[key]) for key in sorted(params))
        return hashlib.sha1((to_sign + secret).encode()).hexdigest()

    @api.model
    def _request(self, action, params, files=None):
        cloud, key, secret = self._get_config()
        if not (cloud and key and secret):
            raise UserError(self.env._(
                "Set the Cloudinary cloud name, API key and API secret in Real Estate settings first."))
        params = dict(params, timestamp=int(time.time()))
        data = dict(params, api_key=key, signature=self._sign(params, secret))
        try:
            response = requests.post(
                ENDPOINT % (cloud, action), data=data, files=files, timeout=TIMEOUT)
            payload = response.json()
        except (requests.RequestException, ValueError) as e:
            raise UserError(self.env._("Cloudinary request failed: %s", str(e))) from e
        error = payload.get('error') if isinstance(payload, dict) else None
        if response.status_code != 200 or error:
            message = error.get('message') if isinstance(error, dict) else response.text[:200]
            raise UserError(self.env._("Cloudinary rejected the request: %s", message))
        return payload

    @api.model
    def _upload(self, raw, filename, folder):
        """ Upload image bytes. Returns {'url': https url, 'public_id': ...}. """
        payload = self._request('upload', {'folder': folder}, files={'file': (filename, raw)})
        return {'url': payload['secure_url'], 'public_id': payload['public_id']}

    @api.model
    def _destroy(self, public_id):
        self._request('destroy', {'public_id': public_id})
