"""The Class Charts integration."""
import logging
import asyncio
import json
import aiohttp
import urllib.parse
import voluptuous as vol
from yarl import URL

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_PUPIL_ID,
    CONF_REFRESH_INTERVAL,
    CONF_DAYS_TO_FETCH,
    CONF_SHOW_NO_SCHOOL
)
from .privacy_http import async_classcharts_request, ClassChartsRequestError

_LOGGER = logging.getLogger(__name__)

NEW_LOGIN_URL = "https://www.classcharts.com/parent/login"

class ClassChartsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a multi-step config flow for Class Charts."""

    VERSION = 1

    def __init__(self):
        """Initialize the multi-step memory structures."""
        self.login_data = {}
        self.discovered_students = {}
        self._discovery_error = "invalid_auth"

    async def async_step_user(self, user_input=None):
        """Step 1: Capture credentials using your exact imported constants."""
        errors = {}

        if user_input is not None:
            students = await self._discover_students(
                user_input[CONF_EMAIL], 
                user_input[CONF_PASSWORD]
            )

            if students:
                self.discovered_students = students
                self.login_data = {
                    "email": user_input[CONF_EMAIL],
                    "password": user_input[CONF_PASSWORD]
                }
                
                return await self.async_step_select_student()
            else:
                errors["base"] = self._discovery_error

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_select_student(self, user_input=None):
        """Step 2: Present a clean dropdown list of children."""
        errors = {}

        if user_input is not None:
            selected_id = user_input["student_selection"]
            student_name = self.discovered_students[selected_id]

            final_data = {
                CONF_EMAIL: self.login_data["email"],
                CONF_PASSWORD: self.login_data["password"],
                CONF_PUPIL_ID: selected_id,
                "student_name": student_name,
            }
            self.login_data = {}

            return self.async_create_entry(
                title=f"Class Charts ({student_name})", 
                data=final_data
            )

        return self.async_show_form(
            step_id="select_student",
            data_schema=vol.Schema({
                vol.Required("student_selection"): vol.In(self.discovered_students)
            }),
            errors=errors,
        )

    async def _discover_students(self, email, password):
        """Authenticate, extract dynamic V2 credentials, and scan for pupil mappings."""
        self._discovery_error = "invalid_auth"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Origin": "https://www.classcharts.com",
            "Referer": "https://www.classcharts.com/",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        payload = {
            "_method": "POST",
            "email": email,
            "logintype": "existing",
            "password": password,
            "recaptcha-token": "no-token-available"
        }

        encoded_payload = urllib.parse.urlencode(payload)

        try:
            async with asyncio.timeout(30):
                async with aiohttp.ClientSession() as session:
                    
                    # Submit credentials form handshake
                    async with async_classcharts_request(
                        session, "POST", NEW_LOGIN_URL,
                        data=encoded_payload, 
                        headers=headers,
                    ) as response:
                        if response.status == 429 or response.status >= 500:
                            self._discovery_error = "cannot_connect"
                            return {}
                        if response.status >= 400:
                            return {}

                    # Extract session validation token
                    session_id_token = None
                    cookies = session.cookie_jar.filter_cookies(URL("https://www.classcharts.com"))
                    
                    if "parent_session_credentials" in cookies:
                        raw_cookie_val = cookies["parent_session_credentials"].value
                        unquoted_cookie = urllib.parse.unquote(raw_cookie_val)
                        try:
                            cookie_json = json.loads(unquoted_cookie)
                            session_id_token = cookie_json.get("session_id")
                        except Exception:
                            pass
                    
                    if not session_id_token and "cc-session" in cookies:
                        session_id_token = cookies["cc-session"].value

                    if not session_id_token:
                        _LOGGER.error("Failed to extract valid authorization session tokens from cookie jar.")
                        return {}

                    # Construct exact V2 API headers
                    api_headers = {
                        "Host": "www.classcharts.com",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Authorization": f"Basic {session_id_token}",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://www.classcharts.com/mobile/parent",
                        "User-Agent": headers["User-Agent"]
                    }
                    
                    found_kids = {}

                    # Route 1: Updated V2 parent ping route
                    v2_ping_url = "https://www.classcharts.com/apiv2parent/ping"
                    async with async_classcharts_request(session, "POST", v2_ping_url, data="{}", headers=api_headers) as api_response:
                        if api_response.status in (401, 403):
                            return {}
                        if api_response.status == 429 or api_response.status >= 500:
                            self._discovery_error = "cannot_connect"
                            return {}
                        if api_response.status == 200:
                            json_data = await api_response.json()
                            if isinstance(json_data, dict) and json_data.get("success") in (0, "0", False):
                                return {}
                            
                            # Safely parse data node whether it's a dict or a list
                            if not isinstance(json_data, (dict, list)):
                                raise ValueError
                            data_node = json_data.get("data", {}) if isinstance(json_data, dict) else json_data
                            pupils_list = []
                            
                            if isinstance(data_node, dict):
                                pupils_list = data_node.get("pupils", [])
                            elif isinstance(data_node, list):
                                pupils_list = data_node
                                
                            if not pupils_list and isinstance(json_data, dict):
                                pupils_list = json_data.get("pupils", [])

                            if pupils_list and isinstance(pupils_list, list):
                                for p in pupils_list:
                                    if isinstance(p, dict):
                                        p_id = str(p.get("id") or p.get("pupil_id") or "")
                                        p_name = p.get("name") or p.get("first_name", f"Student {p_id}")
                                        if p_id:
                                            found_kids[p_id] = p_name.strip()

                    # Route 2: Fallback to dedicated explicit pupils list if ping returned nothing
                    if not found_kids:
                        v2_pupils_url = "https://www.classcharts.com/apiv2parent/pupils"
                        async with async_classcharts_request(session, "POST", v2_pupils_url, data="{}", headers=api_headers) as pupils_response:
                            if pupils_response.status != 200:
                                if pupils_response.status not in (401, 403):
                                    self._discovery_error = "cannot_connect"
                                return {}
                            if pupils_response.status == 200:
                                json_data = await pupils_response.json()
                                if isinstance(json_data, dict) and json_data.get("success") in (0, "0", False):
                                    return {}
                                if isinstance(json_data, list):
                                    pupils_list = json_data
                                elif isinstance(json_data, dict):
                                    data_node = json_data.get("data", [])
                                    if isinstance(data_node, list):
                                        pupils_list = data_node
                                    elif isinstance(data_node, dict):
                                        pupils_list = data_node.get("pupils", [])
                                    else:
                                        pupils_list = []
                                    if not pupils_list:
                                        pupils_list = json_data.get("pupils", [])
                                else:
                                    raise ValueError

                                if pupils_list and isinstance(pupils_list, list):
                                    for p in pupils_list:
                                        if isinstance(p, dict):
                                            p_id = str(p.get("id") or p.get("pupil_id") or "")
                                            p_name = p.get("name") or p.get("first_name", f"Student {p_id}")
                                            if p_id:
                                                found_kids[p_id] = p_name.strip()

                    if found_kids:
                        return found_kids
                    
                    self._discovery_error = "unknown"
                    _LOGGER.error("Class Charts returned no supported student profiles.")
                    return {}
                            
        except (aiohttp.ClientError, TimeoutError, ClassChartsRequestError):
            self._discovery_error = "cannot_connect"
            _LOGGER.warning("Could not complete a secure connection to Class Charts.")
            return {}
        except Exception:
            self._discovery_error = "unknown"
            # Exception text/tracebacks may contain responses, URLs or tokens.
            _LOGGER.error("Class Charts returned an unexpected response during setup.")
            return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Link the options flow to the config flow."""
        return ClassChartsOptionsFlowHandler()


class ClassChartsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Class Charts settings."""

    async def async_step_init(self, user_input=None):
        """Manage the actual settings menu."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=options.get(CONF_REFRESH_INTERVAL, 60), # Default to 60 minutes
                ): vol.All(vol.Coerce(int), vol.Range(min=15, max=1440)),
                vol.Optional(
                    CONF_DAYS_TO_FETCH,
                    default=options.get(CONF_DAYS_TO_FETCH, 14),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Optional(
                    "show_completed_homework",
                    default=options.get("show_completed_homework", True),
                ): bool,
                vol.Optional(
                    CONF_SHOW_NO_SCHOOL,
                    default=options.get(CONF_SHOW_NO_SCHOOL, True),
                ): bool,
            }),
        )
