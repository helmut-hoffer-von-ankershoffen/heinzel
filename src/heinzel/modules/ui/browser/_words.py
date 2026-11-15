"""The commit word list of the browser's gate, as data (ADR 0162, safety statement 3).

A control whose name holds one of these words is treated as a commit: it is proposed to the person and runs only
after their yes. The list is a heuristic. A site can name its send button anything; the visible tab, the never-list
and the audit log are what the person relies on besides it.

Words are matched whole, lower-case, after Unicode case folding (`ß` is `ss`), so each form is listed.
"""

from typing import Final

ENGLISH: Final = frozenset({
    "send", "sends", "sending", "submit", "submits",
    "post", "posts", "posting", "publish", "publishes", "publishing",
    "reply", "replies", "replying",
    "buy", "buys", "buying", "purchase", "purchases",
    "order", "orders", "ordering",
    "pay", "pays", "paying", "payment",
    "checkout",
    "book", "books", "booking",
    "transfer", "transfers", "transferring",
    "delete", "deletes", "deleting",
    "remove", "removes", "removing",
    "cancel", "cancels", "cancelling", "canceling",
    "unsubscribe", "unsubscribes",
    "confirm", "confirms", "confirming",
    "accept", "accepts", "accepting",
    "agree", "agrees", "agreeing",
    "sign", "signs", "signing",
    "share", "shares", "sharing",
    "invite", "invites", "inviting",
})  # fmt: skip

GERMAN: Final = frozenset({
    "senden", "sende", "absenden", "abschicken", "schicken", "versenden",
    "posten", "veröffentlichen", "veroeffentlichen",
    "antworten", "antwort",
    "kaufen", "kauf", "bestellen", "bestellung",
    "zahlen", "bezahlen", "zahlung", "zahlungspflichtig", "kostenpflichtig",
    "kasse",
    "buchen", "buchung",
    "überweisen", "ueberweisen", "überweisung", "ueberweisung",
    "löschen", "loeschen", "entfernen",
    "stornieren", "kündigen", "kuendigen", "abbrechen",
    "abbestellen", "abmelden",
    "bestätigen", "bestaetigen",
    "akzeptieren", "annehmen", "zustimmen",
    "unterschreiben", "unterzeichnen", "signieren",
    "teilen", "einladen",
})  # fmt: skip

PHRASES: Final = (("check", "out"),)
"""Commit names written as two words."""

COMMIT_WORDS: Final = frozenset(word.casefold() for word in ENGLISH | GERMAN)

SECRET_FIELD_WORDS: Final = frozenset({
    "password", "passwort", "kennwort", "passcode", "passphrase",
    "otp", "tan", "pin", "cvc", "cvv", "cvc2", "cvv2",
    "einmalcode", "sicherheitscode", "prüfnummer", "pruefnummer", "kartennummer", "kreditkartennummer",
})  # fmt: skip
"""A field whose name holds one of these takes no typing from an agent, whatever its type says."""

SECRET_FIELD_PHRASES: Final = (
    ("one", "time"),
    ("verification", "code"),
    ("security", "code"),
    ("card", "number"),
    ("expiry", "date"),
    ("expiration", "date"),
)
