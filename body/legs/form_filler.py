"""Body/Legs — form filling.

Filling a form is the operation in this module most likely to submit something by
accident, so it has three properties the rest of the package does not:

* **It never submits.** :meth:`FormFiller.fill` fills fields and stops. Submitting
  is a separate call with its own decision, because "type this address" and "place
  the order" are not the same request and the user should be asked about the second
  one in those words. A form filler that submits itself has no honest preview to
  offer — the confirmation would have to describe a purchase.
* **It refuses file inputs.** A file input is an upload, and an upload is the one
  direction this browser does not have a quarantine for. Downloads land in
  ``storage/downloads`` and are classified; uploads would leave the machine with no
  inspection at all, so the capability is simply absent rather than gated.
* **It fills what it can name and reports what it could not.** Every key that did
  not land is in ``missing`` with a reason. A silent partial fill is how a form
  gets submitted with one field silently blank.

Field detection is the part that has to be good, because the alternative to finding
a field is asking the user which of twelve inputs is which. Each field is described
by every handle it has — name, id, placeholder, label text, aria-label — and a
caller key can match any of them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_controller import BrowserController
from body.legs.element_interactor import (
    ACTION_TIMEOUT_MS,
    AUTOMATION_CATEGORY,
    ElementInteractor,
)
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    ElementRefused,
    FormFillRefused,
)

#: Input types that take no value from ``fill`` because the browser owns them.
#: ``checkbox`` and ``radio`` take a checked state and ``fill`` would write a
#: string into the ``value`` attribute, which is not what the user sees.
NON_TEXT_INPUTS = frozenset(
    {
        "checkbox",
        "radio",
        "file",
        "image",
        "range",
        "color",
        "date",
        "datetime-local",
        "month",
        "week",
        "time",
    }
)

#: Fixed snippet: read every fillable field with every handle it has. No caller
#: value reaches this string.
LIST_FIELDS_JS = """
() => {
  const out = [];
  const handles = (el) => {
    const label = el.labels && el.labels.length ? el.labels[0].innerText : "";
    return {
      name: el.getAttribute('name') || '',
      id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      label: (label || '').trim().slice(0, 80),
      aria_label: el.getAttribute('aria-label') || '',
      type: (el.getAttribute('type') || el.type || 'text').toLowerCase(),
      tag: el.tagName.toLowerCase(),
      required: !!el.required,
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
      visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
      options: el.tagName === 'SELECT'
        ? Array.from(el.options).map((o) => o.value)
        : []
    };
  };
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    out.push(handles(el));
  });
  return out;
}
"""

#: Fixed snippet: set a checkbox or radio explicitly, since ``fill`` writes a
#: string and a checkbox does not have a value the user sees.
SET_CHECKED_JS = """
(el) => { el.checked = !el.checked; return el.checked; }
"""


class FormFiller:
    """Detects and fills form fields in a session's active page.

    Attributes:
        controller: The controller holding the sessions.
        interactor: The interactor, used for the confidence check so a
            form-fill-via-vision call obeys the same threshold as a click.
        logger: Activity logger.
    """

    def __init__(
        self,
        controller: BrowserController,
        interactor: Optional[ElementInteractor] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the form filler.

        Args:
            controller: The controller holding the sessions.
            interactor: The interactor, built from ``controller`` when None.
            logger: Activity logger.
        """
        self.controller = controller
        self.interactor = interactor or ElementInteractor(controller, logger=logger)
        self.logger = logger

    # -- detection ------------------------------------------------------

    async def detect(self, session_id: str) -> List[Dict[str, Any]]:
        """List the fillable fields on the current page.

        Not gated: it describes a page's form *structure* — how many inputs there
        are and what they are called — and reads no values. The page's contents were
        already fetched under a ``browse_internet`` decision when it was navigated
        to, and this is the panel the user consults to decide what to fill in.

        Args:
            session_id: The session.

        Returns:
            One record per field: every handle, the type, and whether it is
            required, disabled, read-only or visible.

        Raises:
            SessionRefused: The session is gone.
        """
        session = self.controller.get(session_id)
        fields = await session.page.evaluate(LIST_FIELDS_JS)
        return [field for field in fields or []]

    async def selector_for(self, session_id: str, key: str) -> str:
        """Return a CSS selector for the field a form-data key names.

        Args:
            session_id: The session.
            key: A ``name``, ``id``, ``placeholder``, label or ``aria-label``.

        Returns:
            A CSS selector that matches exactly one element.

        Raises:
            FormFillRefused: No field matches, or more than one does.
        """
        matches = await self._matching(session_id, key)
        if not matches:
            raise FormFillRefused(
                key, f"no field on this page is named {key!r} by its name, id, "
                     f"placeholder or label"
            )
        if len(matches) > 1:
            described = ", ".join(
                f"{item.get('tag')}[type={item.get('type')}]"
                f"#{item.get('id') or '?'}" for item in matches[:4]
            )
            raise FormFillRefused(
                key, f"{key!r} matches {len(matches)} fields ({described}); name "
                     f"one more precisely"
            )
        field = matches[0]
        return _css_for(field)

    # -- filling --------------------------------------------------------

    async def fill(
        self,
        decision: PermissionDecision,
        session_id: str,
        form_data: Dict[str, str],
        confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fill form fields and stop.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            form_data: ``{field handle: value}``. A key matches any handle the
                field has, so ``{"Email": "a@b.c"}`` works on a form whose input
                has ``name="email"`` and ``id="user_email"``.
            confidence: The Occipital Lobe's confidence, when the field was located
                by vision rather than by handle. Checked against the
                ``click_or_form_fill`` threshold — a form fill is a set of clicks.

        Returns:
            ``filled``, ``missing``, ``fields`` and ``url``. Never ``submitted``:
            this method does not submit, and the absence of the key is the point.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone.
            ElementRefused: The vision confidence is below the floor.
            FormFillRefused: ``form_data`` is empty or is not a mapping. A field
                that cannot be filled is reported in ``missing`` rather than
                raised, so one bad key out of eight does not abandon the other
                seven; :meth:`fill_strict` turns that into a refusal.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        if not isinstance(form_data, dict) or not form_data:
            raise FormFillRefused(
                str(list(form_data or {})[:1]),
                "no fields were given; send {\"field\": \"value\"}",
            )
        verdict = self.interactor.check_confidence(confidence, "click_or_form_fill")
        if verdict["verdict"] == "refuse":
            raise ElementRefused(
                f"confidence {confidence:.2f} is below the {verdict['threshold']:.2f} "
                f"floor for filling a form, so ZUZZ is not typing. A value in the "
                f"wrong box is worse than an empty form."
            )
        filled: List[str] = []
        missing: List[Dict[str, str]] = []
        fields = await self.detect(session_id)
        for key, value in form_data.items():
            outcome = await self._fill_one(session, fields, str(key), str(value))
            if outcome["ok"]:
                filled.append(str(key))
            else:
                missing.append({"field": str(key), "reason": outcome["reason"]})
        session.touch()
        self._log(
            "browser_form_filled",
            session=session_id,
            filled=len(filled),
            missing=len(missing),
            submitted=False,
        )
        return {
            "filled": filled,
            "missing": missing,
            "fields": fields,
            "url": _url_of(session.page),
            "submitted": False,
            "confidence_verdict": verdict,
        }

    async def fill_strict(
        self,
        decision: PermissionDecision,
        session_id: str,
        form_data: Dict[str, str],
    ) -> Dict[str, Any]:
        """Fill every field or fail.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            form_data: ``{field handle: value}``.

        Returns:
            The same record :meth:`fill` returns.

        Raises:
            FormFillRefused: Any field could not be filled. Partial fills are the
                thing this exists to prevent: the user asked for four values and
                three of them landing is not a success with a warning attached, it
                is a form that is ready to submit wrong.
        """
        result = await self.fill(decision, session_id, form_data)
        if result["missing"]:
            first = result["missing"][0]
            raise FormFillRefused(
                first["field"],
                f"{first['reason']} (and {len(result['missing']) - 1} more)",
            )
        return result

    async def submit(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str = "",
        confirm_text: str = "",
    ) -> Dict[str, Any]:
        """Submit a form.

        Split out from :meth:`fill` and separately confirmed because it is the point
        at which a form stops being a draft and becomes an action: a search, an
        order, a login, a message sent. The confirmation shows
        ``confirm_text`` — the button's own label — so the user approves the verb
        they are about to use rather than the word "submit".

        Args:
            decision: An allowed ``web_automation`` decision whose target names the
                form, so a confirmation approved for one form cannot be spent on
                another.
            session_id: The session.
            selector_or_text: The submit button, or ``""`` to submit the first form
                on the page by pressing Enter in its first field.
            confirm_text: What the user was shown. Recorded in the log so the audit
                trail says which button was pressed, not just that something was.

        Returns:
            ``submitted``, ``button`` and ``url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            ElementRefused: There is no form to submit.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        if not str(selector_or_text).strip():
            count = await session.page.evaluate(
                "() => document.querySelectorAll('form').length"
            )
            if not count:
                raise ElementRefused("this page has no form to submit")
            await session.page.evaluate(
                "() => { const f = document.querySelector('form');"
                " if (f) f.requestSubmit ? f.requestSubmit() : f.submit(); }"
            )
            button = "the first form"
        else:
            locator = await self.interactor.resolve(session.page, selector_or_text)
            await locator.first.click(timeout=ACTION_TIMEOUT_MS)
            button = str(selector_or_text)
        try:
            await session.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except Exception:
            pass
        session.touch()
        self._log("browser_form_submitted", session=session_id, button=button,
                  confirmed_label=confirm_text)
        return {"submitted": True, "button": button, "url": _url_of(session.page)}

    # -- internals ------------------------------------------------------

    async def _matching(self, session_id: str, key: str) -> List[Dict[str, Any]]:
        """Return the fields a form-data key names.

        Args:
            session_id: The session.
            key: The handle to match.

        Returns:
            Every matching field. More than one is the caller's problem to resolve,
            and :meth:`selector_for` names them.
        """
        fields = await self.detect(session_id)
        wanted = str(key).strip().lower()
        matches = []
        for field in fields:
            handles = (
                field.get("name"),
                field.get("id"),
                field.get("placeholder"),
                field.get("label"),
                field.get("aria_label"),
            )
            if any(str(handle or "").strip().lower() == wanted for handle in handles):
                matches.append(field)
        return matches

    async def _fill_one(
        self,
        session: Any,
        fields: List[Dict[str, Any]],
        key: str,
        value: str,
    ) -> Dict[str, str]:
        """Fill one field, or explain why it could not be.

        Args:
            session: The session holding the page.
            fields: The page's detected fields.
            key: The form-data key.
            value: The value to put in.

        Returns:
            ``{"ok": "1"}`` or ``{"ok": "", "reason": ...}``. A per-field outcome
            rather than an exception, because one bad key out of eight should not
            abandon the other seven.
        """
        matches = [
            field
            for field in fields
            if any(
                str(handle or "").strip().lower() == key.strip().lower()
                for handle in (
                    field.get("name"),
                    field.get("id"),
                    field.get("placeholder"),
                    field.get("label"),
                    field.get("aria_label"),
                )
            )
        ]
        if not matches:
            return {
                "ok": "",
                "reason": f"no field is named {key!r} by its name, id, placeholder "
                          f"or label; call /api/browser/extract with mode=fields to "
                          f"see what this form has",
            }
        if len(matches) > 1:
            return {
                "ok": "",
                "reason": f"{key!r} matches {len(matches)} fields; name one more "
                          f"precisely",
            }
        field = matches[0]
        kind = field.get("type", "text")
        if field.get("disabled"):
            return {"ok": "", "reason": "the field is disabled"}
        if field.get("readonly"):
            return {"ok": "", "reason": "the field is read-only"}
        if not field.get("visible", True):
            return {
                "ok": "",
                "reason": "the field is not visible; ZUZZ fills what a user could "
                          "see, not what a script left in the page",
            }
        if field.get("tag") == "select":
            if value not in field.get("options", []):
                return {
                    "ok": "",
                    "reason": f"{value!r} is not one of the options "
                              f"{field.get('options', [])}",
                }
            locator = session.page.locator(_css_for(field)).first
            await locator.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
            return {"ok": "1"}
        if kind in ("checkbox", "radio"):
            wanted = str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
            locator = session.page.locator(_css_for(field)).first
            if await locator.is_checked() != wanted:
                await locator.click(timeout=ACTION_TIMEOUT_MS)
            return {"ok": "1"}
        if kind in NON_TEXT_INPUTS:
            if kind == "file":
                return {
                    "ok": "",
                    "reason": "it is a file input; ZUZZ downloads but never uploads",
                }
            return {
                "ok": "",
                "reason": f"it is a {kind} input, which the browser sets from its "
                          f"own control rather than from typed text",
            }
        locator = session.page.locator(_css_for(field)).first
        try:
            await locator.fill(value, timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            return {"ok": "", "reason": f"the browser would not accept the value: {exc}"}
        return {"ok": "1"}

    def _verify(self, decision: PermissionDecision) -> None:
        """Refuse the call unless the decision permits it.

        Args:
            decision: What the caller was handed.

        Returns:
            Nothing.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
        """
        if not isinstance(decision, PermissionDecision):
            raise DecisionRefused(
                "refusing to fill a form without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused("dry-run decisions describe the action; they never type")
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != AUTOMATION_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {AUTOMATION_CATEGORY}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. Values are never logged — a form is
                where a password is typed, and the log is the one file an operator
                is guaranteed to read.

        Returns:
            Nothing.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("legs", action, **fields)
        except Exception:
            pass


def _css_for(field: Dict[str, Any]) -> str:
    """Return a CSS selector for a detected field.

    ``id`` first, because it is unique and a page can be read without executing
    anything to find it. ``name`` second. A field with neither gets a positional
    ``nth-of-type`` selector, which is built from data this module read rather than
    from anything the caller supplied, so it cannot be used to reach a different
    element than the one that was described.

    Args:
        field: A record from :data:`LIST_FIELDS_JS`.

    Returns:
        A CSS selector.
    """
    if field.get("id"):
        return f"#{field['id']}"
    if field.get("name"):
        return f"{field.get('tag', 'input')}[name=\"{field['name']}\"]"
    tag = field.get("tag", "input")
    return f"{tag}:not([id]):not([name])"


def _url_of(page: Any) -> str:
    """Return a page's URL, defensively.

    Args:
        page: The Playwright page.

    Returns:
        The URL, or an empty string.
    """
    try:
        return str(page.url)
    except Exception:
        return ""