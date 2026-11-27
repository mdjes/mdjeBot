"""Persian text for Pillow without a text-shaping library.

Pillow draws characters one by one, left to right, so Persian comes out as separate, reversed letters. ``visual()``
turns a logical string into what must be drawn: letters replaced by their joined presentation forms and the words in
right-to-left order, with numbers and Latin words kept readable. It covers the Persian alphabet, which is all the
health cards use; it is not a general bidi implementation.
"""

from __future__ import annotations

# letter: (isolated, final, initial, medial); right-joining letters have only the first two.
_FORMS: dict[str, tuple[int, ...]] = {
    "ء": (0xFE80,),
    "آ": (0xFE81, 0xFE82),
    "أ": (0xFE83, 0xFE84),
    "ؤ": (0xFE85, 0xFE86),
    "إ": (0xFE87, 0xFE88),
    "ئ": (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),
    "ا": (0xFE8D, 0xFE8E),
    "ب": (0xFE8F, 0xFE90, 0xFE91, 0xFE92),
    "ة": (0xFE93, 0xFE94),
    "ت": (0xFE95, 0xFE96, 0xFE97, 0xFE98),
    "ث": (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),
    "ج": (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),
    "ح": (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),
    "خ": (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),
    "د": (0xFEA9, 0xFEAA),
    "ذ": (0xFEAB, 0xFEAC),
    "ر": (0xFEAD, 0xFEAE),
    "ز": (0xFEAF, 0xFEB0),
    "س": (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4),
    "ش": (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),
    "ص": (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),
    "ض": (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),
    "ط": (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4),
    "ظ": (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    "ع": (0xFEC9, 0xFECA, 0xFECB, 0xFECC),
    "غ": (0xFECD, 0xFECE, 0xFECF, 0xFED0),
    "ف": (0xFED1, 0xFED2, 0xFED3, 0xFED4),
    "ق": (0xFED5, 0xFED6, 0xFED7, 0xFED8),
    "ك": (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC),
    "ل": (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    "م": (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4),
    "ن": (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),
    "ه": (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),
    "و": (0xFEED, 0xFEEE),
    "ى": (0xFEEF, 0xFEF0),
    "ي": (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
    "پ": (0xFB56, 0xFB57, 0xFB58, 0xFB59),
    "چ": (0xFB7A, 0xFB7B, 0xFB7C, 0xFB7D),
    "ژ": (0xFB8A, 0xFB8B),
    "ک": (0xFB8E, 0xFB8F, 0xFB90, 0xFB91),
    "گ": (0xFB92, 0xFB93, 0xFB94, 0xFB95),
    "ی": (0xFBFC, 0xFBFD, 0xFBFE, 0xFBFF),
}
# lam followed by an alef is always written as one ligature: (isolated, final)
_LAM_ALEF = {"ا": (0xFEFB, 0xFEFC), "آ": (0xFEF5, 0xFEF6), "أ": (0xFEF7, 0xFEF8), "إ": (0xFEF9, 0xFEFA)}
_ZWNJ = "‌"
_MARKS = {chr(c) for c in range(0x064B, 0x0660)} | {"ٰ"}  # harakat: no glyph of their own here
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _dual(char: str) -> bool:
    return len(_FORMS.get(char, ())) == 4


def _joins(char: str) -> bool:
    return char in _FORMS and char != "ء"


def has_persian(text: str) -> bool:
    return any(char in _FORMS for char in text)


def shape(word: str) -> str:
    """The joined presentation forms of one word, still in logical (first letter first) order."""
    chars = [c for c in word if c not in _MARKS]
    out: list[str] = []
    i = 0
    prev: str | None = None  # the previous letter in this word, when it can join forward
    while i < len(chars):
        char = chars[i]
        if char == _ZWNJ:
            prev = None
            i += 1
            continue
        if char not in _FORMS:
            out.append(char)
            prev = None
            i += 1
            continue
        joined_before = prev is not None and _dual(prev)
        if char == "ل" and i + 1 < len(chars) and chars[i + 1] in _LAM_ALEF:
            isolated, final = _LAM_ALEF[chars[i + 1]]
            out.append(chr(final if joined_before else isolated))
            prev = None
            i += 2
            continue
        following = chars[i + 1] if i + 1 < len(chars) else None
        joins_next = _dual(char) and following is not None and following != _ZWNJ and _joins(following)
        forms = _FORMS[char]
        if len(forms) == 1:
            index = 0
        elif len(forms) == 2:
            index = 1 if joined_before else 0
        elif joined_before and joins_next:
            index = 3
        elif joined_before:
            index = 1
        elif joins_next:
            index = 2
        else:
            index = 0
        out.append(chr(forms[index]))
        prev = char
        i += 1
    return "".join(out)


def visual(text: str) -> str:
    """What to hand to ``ImageDraw.text`` so ``text`` reads right to left; Latin and numbers stay as they are."""
    words = text.translate(_DIGITS).split(" ")
    pieces = [shape(word)[::-1] if has_persian(word) else word for word in words]
    return " ".join(reversed(pieces))
