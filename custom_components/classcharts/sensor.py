from __future__ import annotations
import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class CCHomeworkSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, name, key):
        super().__init__(coordinator)
        self._key = key
        student_label = entry.data.get("student_name") or entry.data.get("pupil_id")
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_hw_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, 
            "name": f"Class Charts ({student_label})"
        }

    @property
    def native_value(self):
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return 0
        homework = self.coordinator.data.get("homework", {})
        meta = homework.get("meta", {})
        return meta.get(self._key, 0)  
        
    @property
    def extra_state_attributes(self):
        """Only store outstanding homework array and cap it to prevent DB bloat."""
        if self._key != "this_week_outstanding_count":
            return {}

        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return {}

        hw = self.coordinator.data.get("homework", {})
        raw_list = hw.get("data", [])

        return {"homework_list": raw_list[:15]}

class CCLessonSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, lesson_type):
        super().__init__(coordinator)
        self._lesson_type = lesson_type
        student_label = entry.data.get("student_name") or entry.data.get("pupil_id")
        self._attr_name = f"{lesson_type.capitalize()} Lesson"
        self._attr_unique_id = f"{entry.entry_id}_lesson_{lesson_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, 
            "name": f"Class Charts ({student_label})"
        }
        self._attr_icon = "mdi:book-education"

    @property
    def native_value(self):
        """Calculates current or next lesson based on today's timetable schedule."""
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return "Unknown"

        timetable = self.coordinator.data.get("timetable", {})
        today_str = datetime.now().strftime("%Y-%m-%d")
        lessons = timetable.get(today_str, [])

        if not lessons or not isinstance(lessons, list):
            return "No Lessons"

        current_time = datetime.now().time()
        current_lesson = None
        next_lesson = None

        for lesson in lessons:
            try:
                start_str = lesson.get("start_time") or lesson.get("start")
                end_str = lesson.get("end_time") or lesson.get("end")

                if not start_str or not end_str:
                    continue

                start_time = datetime.strptime(start_str[:5], "%H:%M").time()
                end_time = datetime.strptime(end_str[:5], "%H:%M").time()

                if start_time <= current_time <= end_time:
                    current_lesson = lesson
                elif start_time > current_time and next_lesson is None:
                    next_lesson = lesson
            except (ValueError, TypeError):
                continue

        target_lesson = current_lesson if self._lesson_type == "current" else next_lesson

        if not target_lesson:
            return "None"

        return (
            target_lesson.get("subject_name") 
            or target_lesson.get("name") 
            or target_lesson.get("lesson_name") 
            or "Unknown Lesson"
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Expose teacher, room, and timings as extra attributes."""
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return {}

        timetable = self.coordinator.data.get("timetable", {})
        today_str = datetime.now().strftime("%Y-%m-%d")
        lessons = timetable.get(today_str, [])

        if not lessons or not isinstance(lessons, list):
            return {}

        current_time = datetime.now().time()
        current_lesson = None
        next_lesson = None

        for lesson in lessons:
            try:
                start_str = lesson.get("start_time") or lesson.get("start")
                end_str = lesson.get("end_time") or lesson.get("end")

                if not start_str or not end_str:
                    continue

                start_time = datetime.strptime(start_str[:5], "%H:%M").time()
                end_time = datetime.strptime(end_str[:5], "%H:%M").time()

                if start_time <= current_time <= end_time:
                    current_lesson = lesson
                elif start_time > current_time and next_lesson is None:
                    next_lesson = lesson
            except (ValueError, TypeError):
                continue

        target_lesson = current_lesson if self._lesson_type == "current" else next_lesson

        if not target_lesson:
            return {}

        return {
            "teacher": target_lesson.get("teacher_name") or target_lesson.get("teacher"),
            "room": target_lesson.get("room_name") or target_lesson.get("room"),
            "start_time": target_lesson.get("start_time") or target_lesson.get("start"),
            "end_time": target_lesson.get("end_time") or target_lesson.get("end"),
            "subject": target_lesson.get("subject_name") or target_lesson.get("name"),
        }

class CCBehaviourSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, name, sensor_type) -> None:
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        student_label = entry.data.get("student_name") or entry.data.get("pupil_id")
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_behaviour_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, 
            "name": f"Class Charts ({student_label})"
        }
        self._attr_native_unit_of_measurement = "pts"
        self._attr_state_class = "measurement"

        if sensor_type == "positive":
            self._attr_icon = "mdi:thumb-up"
        elif sensor_type == "negative":
            self._attr_icon = "mdi:thumb-down"
        else:
            self._attr_icon = "mdi:star-circle"

    @property
    def native_value(self):
        """Sums values from the pre-scoped academic year timeline payload."""
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return 0

        data = self.coordinator.data.get("behaviour_data", {}).get("data", {})
        timeline = data.get("timeline", [])
        
        total_pos = sum(item.get("positive", 0) for item in timeline)
        total_neg = sum(item.get("negative", 0) for item in timeline)
        
        if self._sensor_type == "balance":
            return (total_pos - total_neg)
        elif self._sensor_type == "positive":
            return total_pos
        elif self._sensor_type == "negative":
            return total_neg
        else:
            return total_pos

    @property
    def extra_state_attributes(self) -> dict:
        """Pulls detailed logs from /activity and summary reasons."""
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return {}

        activity_list = self.coordinator.data.get("activity_data", {}).get("data", {})
        behaviour_data = self.coordinator.data.get("behaviour_data", {}).get("data", {})

        recent_log = [
            {
                "reason": item.get("reason"),
                "points": item.get("score"),
                "teacher": item.get("teacher_name"),
                "date": item.get("timestamp")
            } for item in activity_list[:5]
        ]

        return {
            "recent_activity": recent_log,
            "positive_reasons": behaviour_data.get("positive_reasons", {}),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Class Charts sensors cleanly using the unified CC class."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        CCHomeworkSensor(coordinator, entry, "Outstanding Homework", "this_week_outstanding_count"),
        CCHomeworkSensor(coordinator, entry, "Homework Due", "this_week_due_count"),
        CCHomeworkSensor(coordinator, entry, "Completed Homework", "this_week_completed_count"),
        CCLessonSensor(coordinator, entry, "current"),
        CCLessonSensor(coordinator, entry, "next"),
        CCBehaviourSensor(coordinator, entry, "Behaviour Balance", "balance"),
        CCBehaviourSensor(coordinator, entry, "Behaviour Positive", "positive"),
        CCBehaviourSensor(coordinator, entry, "Behaviour Negative", "negative"),
    ], update_before_add=True)
