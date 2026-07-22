from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import mistralai, deepgram
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import httpx
import os
from difflib import get_close_matches

# Load environment variables
load_dotenv(".env")

class Assistant(Agent):
    """Basic voice assistnant with Airbnb booking capabilities"""

    def __init__(self):
        super().__init__(
            tools=[
                EndCallTool(
                    delete_room=True,
                    end_instructions="Thank the customer by name, confirm their appointment time once more, and say a brief goodbye.",
                    extra_description=(
                        "Use this tool ONLY immediately after create_booking has succeeded and you have "
                        "told the customer their booking is confirmed. Do NOT use this tool for any other "
                        "reason - not if the customer says goodbye without booking, not if they're just "
                        "browsing the menu, and not if booking has not happened yet in this conversation."
                    ),
                )
            ],
            instructions="""You are a helpful and friendly receptionist for a nail salon, but keep dialogue short and simple.
            You can help users navigate the menu, search for prices, check availability, and book appointments.
            Keep your responses concise and natural, as if having a conversation.

            SPEECH FORMATTING - YOUR RESPONSES ARE SPOKEN ALOUD, NOT READ AS TEXT:
            - NEVER use markdown formatting: no **bold**, no bullet points/dashes, no headers, no numbered lists
            - Speak naturally in full sentences, the way a person would say it out loud on the phone
            - Instead of listing details as bullet points, weave them into a sentence.
              Bad: "- Service: Gel Manicure - $35\n- Date: July 21\n- Time: 11:30 AM"
              Good: "That's a gel manicure for thirty-five dollars on July 21st at 11:30 AM."
              Good: No use of "\n"
            - Say prices as natural speech ("thirty-five dollars," not "$35")
            - Say times naturally ("eleven thirty," not "11:30 AM"), but ALWAYS use the exact time value
              a tool gave you - never convert, reformat, or recompute a time yourself, even between
              12-hour and 24-hour format. Tools already return times in the format you should say.

            STRICT RULES - NEVER GUESS OR MAKE UP INFORMATION:
            - NEVER state a service name, price, or availability unless it came directly from a tool result.
            - If the customer names a service, you MUST call search_menu (or search_and_check_availability)
              before saying anything about it - including confirming that it exists.
            - Tool results that find a match will say "MATCHED SERVICE: <name> - <price>". ALWAYS use that
              exact service name in your responses and booking confirmations - never repeat the customer's
              original wording or a misheard/misspelled version of it, even if it sounds close.
            - If a tool result says "NO MATCH FOUND," tell the customer you don't have that service and ask
              them to clarify - do NOT invent a price, description, or pretend it matched something.
            - NEVER state an appointment time (e.g. "earliest slot is 2pm") unless it came directly from a
              check_available_slots or search_and_check_availability call made in this turn or a very recent
              turn. Do not estimate or assume times.
            - Whenever the customer names or asks about a SPECIFIC time (e.g. "can I get 2:30?", "is 9am
              open?"), you MUST call check_available_slots (or search_and_check_availability) again in that
              same turn, passing that time as requested_time in HH:MM 24-hour format, before saying whether
              it's available. Do NOT compare their requested time against a list you already showed earlier
              in the conversation and answer from memory - always re-check.
            - If the tool says a requested time is NOT available and gives you a closest available time, use
              EXACTLY that time when telling the customer the next available option. Do not round, estimate,
              or pick a different "close" time yourself - the tool has already computed the real closest slot.
            - If you are not sure whether you already have fresh tool data, call the tool again rather than guessing.

            IMPORTANT BOOKING INSTRUCTIONS:
            - When a customer wants to book an appointment, you MUST use the create_booking function
            - ALWAYS collect THREE pieces of information before calling create_booking:
              1. Customer's full name
              2. Customer's phone number
              3. Preferred time slot
            - Before calling create_booking, read back the name, phone, service (using its exact matched
              name), date, and time to the customer and ask them to confirm. Only call create_booking AFTER
              they confirm - never call it first and ask for confirmation afterward.
            - Call create_booking EXACTLY ONCE per confirmed appointment. Once you have called it
              successfully, do NOT call it again for the same appointment - even if the customer says "yes,"
              "confirm," or repeats their details again afterward. Treat a successful booking as done.
            - Format start_time as ISO 8601: YYYY-MM-DDTHH:MM:SS (e.g., 2026-07-21T09:00:00)
            - NEVER calculate or guess the start_time yourself from what the customer said out loud
              (e.g. "two thirty"). Always use the exact "start_time to use" value that check_available_slots
              or search_and_check_availability returned for the slot the customer picked. If you're not sure
              which exact value corresponds to the time they chose, call check_available_slots again first.
            - Use America/Los_Angeles as the default timezone unless the user specifies otherwise
            - Do NOT call create_booking unless you have all three: name, phone, and time
            - If create_booking fails because the slot is no longer available, call check_available_slots
              again to get a fresh list before offering the customer a new time - do not guess the next slot
            - After create_booking succeeds, use the end_call tool right away. Do not ask "anything else?"
              first and do not wait for the customer to say goodbye - the end_call tool's own message will
              confirm the appointment details and say goodbye for you.
            - Do NOT use end_call for any reason other than a just-completed booking - not for general
              goodbyes, not mid-conversation, and not if the customer hasn't booked anything yet"""
        )

        self.CAL_API_KEY = os.getenv("CAL_API_KEY")
        self.CAL_EVENT_TYPE_ID = os.getenv("CAL_EVENT_TYPE_ID")
        self.CAL_API_BASE = os.getenv("CAL_API_BASE", "https://api.cal.com/v2")

        # Base headers WITHOUT a version - each endpoint below sets its own
        # cal-api-version, since /slots, /bookings (create), and /bookings/cancel
        # each expect a different version value.
        self.CAL_HEADERS = {
            "Authorization": f"Bearer {self.CAL_API_KEY}",
            "Content-Type": "application/json",
        }

        if not self.CAL_API_KEY:
            print("WARNING: CAL_API_KEY not loaded - check .env.local path/contents")
        if not self.CAL_EVENT_TYPE_ID:
            print("WARNING: CAL_EVENT_TYPE_ID not loaded - check .env.local path/contents")

        # Nail Salon Database
        self.menu = {
            "acrylic": [
                {
                    "id": "ac001",
                    "name": "Regular Set",
                    "price": "Starting at 38",
                },
                {
                    "id": "ac002",
                    "name": "White Tips",
                    "price": "Starting at 45"
                },
                {
                    "id": "ac003",
                    "name": "Pink & White",
                    "price": "Starting at 55"
                },
                {
                    "id": "ac004",
                    "name": "Crystal Set",
                    "price": "Starting at 50"
                },
                {
                    "id": "ac005",
                    "name": "Gel Color Set",
                    "price": "Starting at 50"
                },
                {
                    "id": "ac006",
                    "name": "Ombre",
                    "price": "Starting at 55"
                },
                {
                    "id": "ac006",
                    "name": "Acrylic Removal",
                    "price": 15
                },
            ],
            "dipping powder": [
                {
                    "id": "dp001",
                    "name": "Color Only",
                    "price": 40
                },
                {
                    "id": "dp002",
                    "name": "With Manicure",
                    "price": 45
                },
            ],
            "filling": [
                {
                    "id": "fl001",
                    "name": "Regular Fill",
                    "price": "Starting at 35"
                },
                {
                    "id": "fl002",
                    "name": "Pink",
                    "price": "Starting at 40"
                },
                {
                    "id": "fl003",
                    "name": "Pink & White",
                    "price": "Starting at 45"
                },
                {
                    "id": "fl004",
                    "name": "Crystal",
                    "price": "Starting at 40"
                },
                {
                    "id": "fl005",
                    "name": "Color Gel",
                    "price": "Starting at 25"
                },
            ],
            "waxing": [
                {
                    "id": "wx001",
                    "name": "Eyebrows Wax",
                    "price": 12
                },
                {
                    "id": "wx002",
                    "name": "Upper Lip Wax",
                    "price": 7
                },
                {
                    "id": "wx003",
                    "name": "Chin Wax",
                    "price": 7
                },
                {
                    "id": "wx004",
                    "name": "Bikini Wax",
                    "price": 32
                },
                {
                    "id": "wx005",
                    "name": "Brazilian Wax",
                    "price": 42
                },
                {
                    "id": "wx006",
                    "name": "Under Arms Wax",
                    "price": 17
                },
                {
                    "id": "wx007",
                    "name": "Half Arms Wax",
                    "price": "Starting at 22"
                },
                {
                    "id": "wx008",
                    "name": "Full Arms Wax",
                    "price": "Starting at 32"
                },
                {
                    "id": "wx009",
                    "name": "Half Legs Wax",
                    "price": "Starting at 27"
                },
                {
                    "id": "wx010",
                    "name": "Full Legs Wax",
                    "price": "Starting at 42"
                },
                {
                    "id": "wx011",
                    "name": "Back Wax",
                    "price": 37
                },
                {
                    "id": "wx012",
                    "name": "Chest Wax",
                    "price": 27
                },
                {
                    "id": "wx013",
                    "name": "Facial Wax",
                    "price": 32
                },
            ],
            "facial": [
                {
                    "id": "fa001",
                    "name": "Classic European",
                    "price": 75,
                    "description": "This highly effective facial includes a thorough skin diagnostic, deep pore cleansing to remove impurities, technical exfoliation, extractions, aroma therapy, neck and décolleté massage, followed by the ultimate custom blended mask, moisturizer and protection.",
                    "time": 60
                },
                {
                    "id": "fa002",
                    "name": "ACNE Solution Treatment",
                    "price": 85,
                    "description": "Acne solution treatment plus indulge in over an hour of blissful pampering. This highly effective facial includes a thorough skin diagnostic, deep pore cleansing to remove impurities, technical exfoliation, extractions, aroma therapy, neck and décolleté massage, followed by the ultimate custom blended mask, moisturizer and protection.",
                    "time": 80
                },
            ],
            "pedicure": [
                {
                    "id": "pd001",
                    "name": "Regular Spa Pedicure",
                    "price": 28,
                    "description": "Nails trimming and shaping, Cuticles grooming, Gentle exfoliating sugar scrub, Massage, Hot towels, Polishing",
                },
                {
                    "id": "pd002",
                    "name": "Gel Pedicure",
                    "price": 38,
                    "description": "Nails trimming and shaping, Cuticles grooming, Gentle exfoliating sugar scrub, Massage, Hot towels, Polishing with Gel",
                },
                {
                    "id": "pd003",
                    "name": "Deluxe Spa Pedicure",
                    "price": 42,
                    "description": "Nails trimming and shaping, Cuticles grooming, Gentle exfoliating sugar scrub, 10 Minute Massage, Hot towels, Polishing, Callus treatment, Hydrating paraffin treatment",
                },
                {
                    "id": "pd004",
                    "name": "Royal Spa Pedicure",
                    "price": 47,
                    "description": "Nails trimming and shaping, Cuticles grooming, Gentle exfoliating sugar scrub, 15 Minute Massage, Hot towels, Polishing, Callus treatment, Hydrating paraffin treatment, Moisturizing spa foot mask",
                },
            ],
            "manicure": [
                {
                    "id": "mn001",
                    "name": "Regular Manicure",
                    "price": 25,
                    "description": "Nails trimming and shaping, Cuticles grooming, Polishing",
                },
                {
                    "id": "mn002",
                    "name": "Gel Manicure",
                    "price": 35,
                    "description": "Nails trimming and shaping, Cuticles grooming, Polishing with Gel",
                },
                {
                    "id": "mn003",
                    "name": "Regular Manicure",
                    "price": 35,
                    "description": "Nails trimming and shaping, Cuticles grooming, Polishing, Gentle exfoliating sugar scrub, Hydratinhg paraffin treatment, Hot Towels",
                },
            ],
            "polish change": [
                {
                    "id": "pc001",
                    "name": "Regular Polish Change",
                    "price": "Starting at 10",
                },
                {
                    "id": "pc002",
                    "name": "Gel Polish Change",
                    "price": 20,
                }
            ],
            "gel polish removal": {
                "id": "gp001",
                "name": "Gel Polish Removal",
                "price": 7,
            },
            "callus removal": {
                "id": "cr001",
                "name": "Callus Removal",
                "price": 7,
            },
            "paraffin treatment": {
                "id": "pt001",
                "name": "Paraffin Treatment",
                "price": 10,
            },
            "nail design":{
                "id": "nd001",
                "name": "Nail Design",
                "price": "Starting at 5",
            },
            "french tip": {
                "id": "ft001",
                "name": "French Tips",
                "price": "Starting at 5",
            },
            "massage": {
                "id": "ms001",
                "name": "Massage (15 minutes)",
                "price": 12,
            },
            "eyelash extension": [
                {
                    "id": "ee001",
                    "name": "Individual (One-by-One)",
                    "price": 100,
                },
                {
                    "id": "ee002",
                    "name": "Fill",
                    "price": 50,
                },
                {
                    "id": "ee003",
                    "name": "Group of Two",
                    "price": 85,
                },
            ],
        }

        # Track Bookings
        self.bookings = []
        self.last_booking = None  # tracks the most recent successful booking to prevent double-booking
        self.slot_cache = {}  # (date, timezone) -> {local_time "HH:MM": exact_iso_start} from the last check_available_slots call

    def _find_menu_item(self, item: str) -> str:
        normalized_query = item.lower().strip()
        
        # Build a list of all service names for fuzzy matching
        all_services = []
        service_map = {}
        
        for _, services in self.menu.items():
            if isinstance(services, list):
                for service in services:
                    if isinstance(service, dict):
                        name = service.get("name", "")
                        all_services.append(name.lower())
                        service_map[name.lower()] = service
            elif isinstance(services, dict):
                name = services.get("name", "")
                all_services.append(name.lower())
                service_map[name.lower()] = services
        
        # Try exact substring match first
        for service_name, service in service_map.items():
            if normalized_query in service_name or service_name in normalized_query:
                price = service.get("price")
                return f"MATCHED SERVICE: {service.get('name')} - ${price}. Always refer to this service by this exact name, not the customer's original wording."

        # Fall back to fuzzy matching for misspellings/STT errors only -
        # cutoff raised to 0.75 so loosely-related terms don't false-positive
        close_matches = get_close_matches(normalized_query, all_services, n=1, cutoff=0.75)
        if close_matches:
            matched_service = service_map[close_matches[0]]
            price = matched_service.get("price")
            return f"MATCHED SERVICE: {matched_service.get('name')} - ${price}. Always refer to this service by this exact name, not the customer's original wording."

        return f"NO MATCH FOUND for '{item}'. Do not confirm or price this service - ask the customer to clarify or rephrase."

    @function_tool
    async def search_and_check_availability(
        self, context: RunContext, item: str, date: str, requested_time: str = ""
    ) -> str:
        """Search menu and check availability simultaneously.
        
        Args:
            item: Service name to search
            date: Date to check availability (YYYY-MM-DD)
            requested_time: Optional. If the customer asked about a specific time,
                pass it here as HH:MM 24-hour format (e.g. "14:00").
        """
        # Run both async operations concurrently
        menu_result, slots_result = await asyncio.gather(
            self.search_menu(context, item),
            self.check_available_slots(context, date, requested_time=requested_time),
            return_exceptions=True
        )
        
        return f"{menu_result}\n{slots_result}"

    @function_tool
    async def get_current_date_and_time(self, context: RunContext) -> str:
        """Get current date and time."""
        current_datetime = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        return f"The current date and time is {current_datetime}"

    @function_tool
    async def search_menu(self, context: RunContext, item: str) -> str:
        """Look up a service in the salon menu and return its price.

        Args:
            item: The service name to search for, such as "gel manicure" or "regular pedicure"
        """
        return self._find_menu_item(item)

    def _to_12hr(self, hhmm: str) -> str:
        """Convert 'HH:MM' 24-hour to a spoken-friendly 12-hour string like '1:30 PM'.
        Doing this in code instead of leaving it for the LLM to convert prevents
        digit-transposition errors like reading '13:30' aloud as 'one thirty-three'."""
        try:
            h, m = (int(x) for x in hhmm.split(":"))
            period = "AM" if h < 12 else "PM"
            h12 = h % 12
            if h12 == 0:
                h12 = 12
            return f"{h12}:{m:02d} {period}"
        except Exception:
            return hhmm

    @function_tool
    async def check_available_slots(
        self, context: RunContext, date: str, timezone: str = "America/Los_Angeles",
        requested_time: str = "", time: str = ""
    ) -> str:
        """Check available appointment slots for a given date.

        Args:
            date: Date to check, in YYYY-MM-DD format
            timezone: IANA timezone string, e.g. "America/Los_Angeles"
            requested_time: Optional. If the customer asked about a SPECIFIC time
                (e.g. they said "two o'clock" or "ten"), pass it here as HH:MM in
                24-hour format (e.g. "14:00", "10:00"). If provided, the response
                will explicitly tell you whether that time is available and, if
                not, the single actual closest available time - computed for you,
                not something you should guess yourself.
        """
        # Accept "time" as an alias in case the model uses the wrong param
        # name - without this, a naming slip silently disables the
        # code-computed closest-time logic and the model falls back to
        # guessing, which is the bug we're trying to eliminate.
        if not requested_time and time:
            requested_time = time

        # Build the day's start/end in the SALON'S LOCAL timezone, then convert
        # to UTC. Using naive "date T00:00:00Z"/"T23:59:59Z" treats the date as
        # a UTC day, which for America/Los_Angeles (UTC-7/-8) actually spans
        # ~5-8 hours into the previous/next local day - causing slots outside
        # the real 9am-5pm local availability window to appear.
        try:
            local_tz = ZoneInfo(timezone)
            day_start_local = datetime.fromisoformat(f"{date}T00:00:00").replace(tzinfo=local_tz)
            day_end_local = datetime.fromisoformat(f"{date}T23:59:59").replace(tzinfo=local_tz)
            start_utc = day_start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc = day_end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            print(f"CAL SLOTS TIMEZONE ERROR: {e}")
            start_utc = f"{date}T00:00:00Z"
            end_utc = f"{date}T23:59:59Z"

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{self.CAL_API_BASE}/slots",
                    headers={**self.CAL_HEADERS, "cal-api-version": "2024-09-04"},
                    params={
                        "eventTypeId": self.CAL_EVENT_TYPE_ID,
                        "start": start_utc,
                        "end": end_utc,
                        "timeZone": timezone,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                body = getattr(e, "response", None)
                print(f"CAL SLOTS ERROR: {e} | body={body.text if body else None}")
                return "I couldn't check availability right now."

        slots_by_date = data.get("data", {})
        slots = slots_by_date.get(date, [])
        if not slots:
            return f"There are no available slots on {date}."

        # Cache the exact local_time -> ISO start_time mapping server-side.
        # create_booking will look up the real ISO time from this cache using
        # the local_time the LLM heard, instead of trusting the LLM to
        # reconstruct or copy the ISO string correctly - that reconstruction
        # is where bugs like "4pm confirmed but 2pm booked" come from.
        slot_map = {}
        display_times = []
        for s in slots[:10]:
            iso_start = s["start"][:19]  # e.g. "2026-07-21T16:00:00"
            local_time = iso_start.split("T")[1][:5]  # "HH:MM" 24-hour
            slot_map[local_time] = iso_start
            display_times.append(local_time)

        self.slot_cache[(date, timezone)] = slot_map

        # Build a spoken-friendly display list (12-hour) alongside the 24-hour
        # values used internally for booking lookups - the LLM should only
        # ever read/say the 12-hour versions, never convert times itself.
        display_12hr = [self._to_12hr(t) for t in display_times]
        base_response = f"Available times on {date} ({timezone}): {', '.join(display_12hr)}"

        if requested_time:
            requested_12hr = self._to_12hr(requested_time)
            if requested_time in slot_map:
                return (
                    f"{base_response}\n\n{requested_12hr} IS available. Tell the customer "
                    f"{requested_12hr} works (say it exactly like that, do not reformat it), "
                    f"then call create_booking with local_time=\"{requested_time}\" once they confirm."
                )
            # Compute the actual closest available time in minutes-of-day terms
            try:
                req_h, req_m = (int(x) for x in requested_time.split(":"))
                req_minutes = req_h * 60 + req_m
                closest = min(
                    display_times,
                    key=lambda t: abs((int(t.split(":")[0]) * 60 + int(t.split(":")[1])) - req_minutes),
                )
                closest_12hr = self._to_12hr(closest)
                return (
                    f"{base_response}\n\n{requested_12hr} is NOT available. The single closest "
                    f"actual available time is {closest_12hr} - say it exactly like that (do not "
                    f"reformat or reconvert it), and use local_time=\"{closest}\" if you call "
                    f"create_booking for it."
                )
            except Exception:
                pass  # fall through to generic response below

        return (
            f"{base_response}\n\n"
            "Read these times to the customer exactly as shown above (e.g. '1:30 PM') - do not "
            "reformat, reconvert, or recompute them. When the customer picks a time, call "
            "create_booking with local_time set to the matching 24-hour HH:MM value: "
            + ", ".join(f"{d12}={d24}" for d12, d24 in zip(display_12hr, display_times))
        )


    @function_tool
    async def create_booking(
        self,
        context: RunContext,
        name: str,
        phonenumber: str,
        date: str,
        local_time: str,
        timezone: str = "America/Los_Angeles",
        notes: str = "",
    ) -> str:
        """Book an appointment.

        Args:
            name: The customer's full name (required)
            date: Appointment date in YYYY-MM-DD format - must match a date already
                checked with check_available_slots in this conversation
            local_time: The exact HH:MM (24-hour) time the customer picked, taken
                directly from the list returned by check_available_slots - do not
                convert or compute this yourself
            timezone: IANA timezone string
            phonenumber: The customer's phone number (required)
            notes: Any additional notes for the booking
        """
        # Resolve the real ISO start time from the cache built by
        # check_available_slots, rather than trusting the LLM to construct or
        # convert the time itself - that reconstruction is where bugs like
        # "4pm confirmed but 2pm booked" come from.
        slot_map = self.slot_cache.get((date, timezone))
        if not slot_map:
            return (
                "I don't have fresh availability data for that date. Please call "
                "check_available_slots again before booking."
            )

        start_time = slot_map.get(local_time)
        if not start_time:
            available = ", ".join(sorted(slot_map.keys()))
            return (
                f"{local_time} isn't in the list of available times I last checked "
                f"({available}). Please call check_available_slots again or pick one "
                f"of those exact times."
            )

        # Guard against double-booking: if we already successfully booked this
        # exact slot in this session, don't call the API again - just repeat
        # the existing confirmation.
        if (
            self.last_booking is not None
            and self.last_booking["start_time"] == start_time
            and self.last_booking["timezone"] == timezone
        ):
            b = self.last_booking
            return (
                f"That appointment is already booked for {start_time}. "
                f"Confirmation code: {b['uid']}."
            )

        # Convert local time to UTC as required by cal.com API
        try:
            local_tz = ZoneInfo(timezone)
            # Parse the start_time as naive datetime in the local timezone
            local_dt = datetime.fromisoformat(start_time)
            # Make it timezone-aware with the specified timezone
            local_dt = local_dt.replace(tzinfo=local_tz)
            # Convert to UTC
            utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
            # Format as ISO 8601 with Z suffix (UTC indicator)
            start_time_utc = utc_dt.isoformat().replace("+00:00", "Z")
        except Exception as e:
            print(f"Error converting timezone: {e}")
            start_time_utc = start_time + "Z"  # Fallback
        
        # Normalize phone number to E.164 format (Cal.com requires this,
        # e.g. +14155551234 - digits only after the leading +)
        digits = "".join(ch for ch in phonenumber if ch.isdigit())
        if phonenumber.strip().startswith("+"):
            phone_e164 = "+" + digits
        elif len(digits) == 10:
            # Assume US number missing country code
            phone_e164 = "+1" + digits
        else:
            phone_e164 = "+" + digits

        payload = {
            "eventTypeId": int(self.CAL_EVENT_TYPE_ID),
            "start": start_time_utc,
            "attendee": {
                "name": name,
                "phoneNumber": phone_e164,
                "timeZone": timezone,
                "language": "en",
            },
            "location": {
                "type": "address",
            },
            "metadata": {"notes": notes} if notes else {},
        }

        max_retries = 3
        retry_delay = 1  # seconds

        for attempt in range(max_retries):
            async with httpx.AsyncClient() as client:
                try:
                    resp = await client.post(
                        f"{self.CAL_API_BASE}/bookings",
                        headers={**self.CAL_HEADERS, "cal-api-version": "2026-02-25"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    booking = data.get("data", {})
                    uid = booking.get("uid", "unknown")
                    self.last_booking = {
                        "start_time": start_time,
                        "timezone": timezone,
                        "uid": uid,
                    }
                    return f"You're booked for {start_time}. Confirmation code: {uid}."
                except httpx.HTTPStatusError as e:
                    # Client errors (400/401/404 etc) won't fix themselves on retry
                    error_detail = e.response.text if e.response is not None else str(e)
                    print(f"CAL BOOKING ERROR ({e.response.status_code}): {error_detail}")
                    if 400 <= e.response.status_code < 500:
                        return "I couldn't complete that booking. That time may already be taken - want to try another slot?"
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    return "I couldn't complete that booking after several attempts. Please try again shortly."
                except httpx.HTTPError as e:
                    print(f"CAL BOOKING NETWORK ERROR (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    return "I couldn't reach the booking system after several attempts. Please try again shortly."


    @function_tool
    async def cancel_booking(
        self, context: RunContext, booking_uid: str, reason: str = "Customer requested cancellation"
    ) -> str:
        """Cancel an existing appointment.

        Args:
            booking_uid: The booking confirmation code/UID to cancel
            reason: Reason for cancellation
        """
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.CAL_API_BASE}/bookings/{booking_uid}/cancel",
                    headers={**self.CAL_HEADERS, "cal-api-version": "2024-08-13"},
                    json={"cancellationReason": reason},
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                body = getattr(e, "response", None)
                print(f"CAL CANCEL ERROR: {e} | body={body.text if body else None}")
                return "I couldn't cancel that booking. Please double-check the confirmation code."

        return f"Your booking ({booking_uid}) has been cancelled."


async def entrypoint(ctx: agents.JobContext):
    """Entry point for the agent"""

    # Configure voice pipeline with the essentials
    session = AgentSession(
        stt=deepgram.STT(model="nova-2"),
        llm=mistralai.LLM(
            model=os.getenv("LLM_CHOICE", "ministral-3b-2512"),
            max_completion_tokens=128,
        ),
        tts=deepgram.TTS(
            model="aura-2-asteria-en",
        ),
    )

    # Start the session
    await session.start(
        room=ctx.room,
        agent=Assistant()
    )

    # Generate initial greeting
    await session.generate_reply(
        user_input="Hello",
    )

if __name__ == "__main__":
    # Run the agent
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))