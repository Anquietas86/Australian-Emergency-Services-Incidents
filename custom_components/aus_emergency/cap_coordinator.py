from __future__ import annotations

import logging
from datetime import timedelta
import aiohttp
import xml.etree.ElementTree as ET
import statistics

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ATTR_SEVERITY,
)

_LOGGER = logging.getLogger(__name__)

CAP_INCIDENTS_XML = "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_cap_incidents.xml"
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


class CFSCAPDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch SA CFS CAP alerts (XML)."""

    def __init__(self, hass: HomeAssistant, update_seconds: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SA CFS CAP Data",
            update_interval=timedelta(seconds=update_seconds),
        )
        self._session: aiohttp.ClientSession | None = None

    async def _async_update_data(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        alerts = []
        try:
            async with self._session.get(CAP_INCIDENTS_XML, timeout=30) as resp:
                if resp.status == 200:
                    xml_string = await resp.text()
                    root = ET.fromstring(xml_string)
                    for alert in root.findall("cap:alert", CAP_NS):
                        alert_id = alert.findtext("cap:identifier", None, CAP_NS)
                        if not alert_id:
                            continue

                        # Process the first info block we find
                        info = alert.find("cap:info", CAP_NS)
                        if info is None:
                            continue

                        # An alert can have multiple areas
                        areas = []
                        for area in info.findall("cap:area", CAP_NS):
                            area_data = {"areaDesc": area.findtext("cap:areaDesc", None, CAP_NS)}
                            
                            # Parse polygon
                            polygons_text = area.findall("cap:polygon", CAP_NS)
                            polygons = []
                            for poly_text_elem in polygons_text:
                                if poly_text_elem.text:
                                    polygons.append(poly_text_elem.text.strip())
                            if polygons:
                                area_data["polygon"] = polygons

                            # Parse circle
                            circles_text = area.findall("cap:circle", CAP_NS)
                            circles = []
                            for circle_text_elem in circles_text:
                                if circle_text_elem.text:
                                    circles.append(circle_text_elem.text.strip())
                            if circles:
                                area_data["circle"] = circles
                            
                            areas.append(area_data)

                        alerts.append(
                            {
                                "id": alert_id,
                                "areas": areas,
                                "headline": info.findtext("cap:headline", None, CAP_NS),
                                "description": info.findtext("cap:description", None, CAP_NS),
                                "instruction": info.findtext("cap:instruction", None, CAP_NS),
                                ATTR_SEVERITY: info.findtext("cap:severity", "Unknown", CAP_NS),
                                "urgency": info.findtext("cap:urgency", "Unknown", CAP_NS),
                                "certainty": info.findtext("cap:certainty", "Unknown", CAP_NS),
                                "event": info.findtext("cap:event", "Unknown", CAP_NS),
                                "effective": info.findtext("cap:effective", None, CAP_NS),
                                "expires": info.findtext("cap:expires", None, CAP_NS),
                            }
                        )
                else:
                    _LOGGER.warning("CFS CAP feed returned HTTP %s", resp.status)
        except Exception as exc:
            _LOGGER.error("Error fetching CFS CAP feed: %s", exc)

        return {"alerts": alerts}

    async def async_close(self):
        if self._session and not self._session.closed:
            await self._session.close()
