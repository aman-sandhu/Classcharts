"""DataUpdateCoordinator for the Class Charts integration."""
import logging
import datetime
import requests
import json
import urllib.parse
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, 
    CONF_PUPIL_ID,
    CONF_DAYS_TO_FETCH,
    HOMEWORK_URL,
    BEHAVIOUR_URL
)
from .privacy_http import classcharts_request

_LOGGER = logging.getLogger(__name__)

# Direct, authenticated V2 endpoints
LOGIN_URL = "https://www.classcharts.com/parent/login"
V2_BASE_URL = "https://www.classcharts.com/apiv2parent"

def sync_get_classcharts_data(email, password, pupil_id, days_to_fetch):
    """Fetch data using the verified V2 Cookie + Auth + Handshake loop."""
    session = requests.Session()
    
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.classcharts.com",
        "Referer": "https://www.classcharts.com/",
    })
    
    try:
        login_payload = {
            "_method": "POST",
            "email": email,
            "logintype": "existing",
            "password": password,
            "recaptcha-token": "no-token-available"
        }
        encoded_login = urllib.parse.urlencode(login_payload)
        
        login_resp = classcharts_request(
            session, "POST", LOGIN_URL,
            data=encoded_login,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15
        )
        if login_resp.status_code >= 400:
            raise UpdateFailed("Class Charts login was unsuccessful.")

        # 2. Extract Authenticated V2 Token from Session Cookies
        cookies_dict = session.cookies.get_dict()
        session_token = None
        
        raw_cookie_data = cookies_dict.get("parent_session_credentials")
        if raw_cookie_data:
            try:
                decoded_cookie_str = urllib.parse.unquote(raw_cookie_data)
                cookie_json = json.loads(decoded_cookie_str)
                session_token = cookie_json.get("session_id")
            except Exception:
                pass
                
        # Safe fallback back into the direct cc-session key
        if not session_token:
            session_token = cookies_dict.get("cc-session")
            
        if not session_token:
            raise UpdateFailed("Failed to extract operational security token from handshake cookies.")

        # Update core global headers for the modern AJAX interface
        session.headers.update({
            "Authorization": f"Basic {session_token}",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.classcharts.com/mobile/parent"
        })

        # 3. Crucial V2 Ping Handshake Initialization
        ping_resp = classcharts_request(session, "POST", f"{V2_BASE_URL}/ping", data="{}", timeout=10)

        if ping_resp.status_code != 200:
            raise UpdateFailed(f"V2 backend gatekeeper rejected API initialization footprint. Code: {ping_resp.status_code}")

        # Check if ping returns token errors
        try:
            ping_json = ping_resp.json()
            if not isinstance(ping_json, dict):
                raise ValueError
            if ping_json.get("success") in (0, "0", False):
                raise UpdateFailed("Class Charts rejected the session.")
        except ValueError:
            raise UpdateFailed("Class Charts returned an invalid login response.") from None

        # 4. Fetch Updated V2 Timetable Data
        full_schedule = {}
        for i in range(days_to_fetch):
            target_date = datetime.date.today() + datetime.timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")

            resp = classcharts_request(
                session, "GET",
                f"{V2_BASE_URL}/timetable/{pupil_id}",
                params={"date": date_str},
                timeout=10
            )
            
            if resp.status_code == 200:
                try:
                    day_data = resp.json()
                    if not isinstance(day_data, dict) or day_data.get("success") in (0, "0", False):
                        raise ValueError
                    lessons = day_data.get("data", [])
                    if not isinstance(lessons, list):
                        raise ValueError
                    full_schedule[date_str] = lessons
                except Exception:
                    raise UpdateFailed("Class Charts returned an invalid timetable response.") from None
            else:
                raise UpdateFailed("Class Charts could not supply the timetable.")

        # 5. Fetch Updated V2 Homework Data
        hw_from = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        hw_to = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        
        hw_resp = classcharts_request(
            session, "GET",
            f"{HOMEWORK_URL}/{pupil_id}",
            params={"display_date": "due_date", "from": hw_from, "to": hw_to},
            timeout=10
        )
        
        homework_data = {"data": [], "meta": {}}
        if hw_resp.status_code == 200:
            try:
                hw_json = hw_resp.json()
                if isinstance(hw_json, list):
                    homework_data = {"data": hw_json, "meta": {}}
                elif isinstance(hw_json, dict):
                    if hw_json.get("success") in (0, "0", False):
                        raise ValueError
                    if "data" in hw_json:
                        homework_data = hw_json
                    else:
                        homework_data = {"data": hw_json.get("homework", hw_json), "meta": hw_json.get("meta", {})}
                else:
                    raise ValueError
                if not isinstance(homework_data.get("data"), list) or not isinstance(homework_data.get("meta", {}), dict):
                    raise ValueError
            except Exception:
                raise UpdateFailed("Class Charts returned an invalid homework response.") from None
        else:
            raise UpdateFailed("Class Charts could not supply homework data.")

        # Calculate Academic Year Date Boundaries (UK: Sept 1st start)
        now = datetime.date.today()
        acad_start_year = now.year if now.month >= 9 else now.year - 1
        acad_start_date = datetime.date(acad_start_year, 9, 1).strftime("%Y-%m-%d")
        acad_end_date = now.strftime("%Y-%m-%d")

        # 6. Fetch Updated V2 Behaviour Data scoped to the Academic Year
        behaviour_resp = classcharts_request(
            session, "GET",
            f"https://www.classcharts.com/apiv2parent/behaviour/{pupil_id}",
            params={"from": acad_start_date, "to": acad_end_date},
            timeout=10
        )
        if behaviour_resp.status_code != 200:
            raise UpdateFailed("Class Charts could not supply behaviour data.")
        behaviour_data = behaviour_resp.json()
        if not isinstance(behaviour_data, dict) or behaviour_data.get("success") in (0, "0", False):
            raise UpdateFailed("Class Charts returned an invalid behaviour response.")
        if not isinstance(behaviour_data.get("data", {}), dict):
            raise UpdateFailed("Class Charts returned an invalid behaviour response.")

        # 7. Fetch Updated V2 Activity Data (Detailed Logs) scoped to Academic Year as well
        activity_resp = classcharts_request(
            session, "GET",
            f"https://www.classcharts.com/apiv2parent/activity/{pupil_id}",
            params={"from": acad_start_date, "to": acad_end_date},
            timeout=10
        )
        if activity_resp.status_code != 200:
            raise UpdateFailed("Class Charts could not supply activity data.")
        activity_data = activity_resp.json()
        if not isinstance(activity_data, dict) or activity_data.get("success") in (0, "0", False):
            raise UpdateFailed("Class Charts returned an invalid activity response.")
        if not isinstance(activity_data.get("data"), list):
            raise UpdateFailed("Class Charts returned an invalid activity response.")

        # Return standardized dictionary for sensors
        return {
            "timetable": full_schedule,
            "homework": homework_data,
            "behaviour_data": behaviour_data,  # This is the summary JSON
            "activity_data": activity_data     # This is the detailed list JSON
        }

    except UpdateFailed as err:
        # Only our fixed, locally defined messages reach Home Assistant.
        raise UpdateFailed(str(err)) from None
    except Exception:
        # Do not forward raw network/API exceptions to logs or HA diagnostics.
        raise UpdateFailed("Unable to retrieve Class Charts data securely.") from None
    finally:
        session.close()


class ClassChartsCoordinator(DataUpdateCoordinator):
    """The wrapper class Home Assistant uses to schedule updates."""

    def __init__(self, hass: HomeAssistant, entry):
        """Initialize the coordinator class."""
        self.entry = entry
        
        self.email = entry.data["email"]
        self.password = entry.data["password"]
        self.pupil_id = entry.data[CONF_PUPIL_ID]
        
        # Bound polling and lookahead even for entries created by older versions.
        refresh_interval = max(15, min(1440, int(entry.options.get("refresh_interval", 60))))
        self.days_to_fetch = max(1, min(30, int(entry.options.get(CONF_DAYS_TO_FETCH, 14))))

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=refresh_interval),
        )

    async def _async_update_data(self):
        """Route the async coordinator request down to our sync fetch loop."""
        return await self.hass.async_add_executor_job(
            sync_get_classcharts_data,
            self.email,
            self.password,
            self.pupil_id,
            self.days_to_fetch
        )
