"""Exact finite decimal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import total_ordering


# Exact arithmetic is retained for manageable results; operations that would
# allocate vastly more digits than their operands fail with a bounded error.
MAX_DECIMAL_EXPANSION_DIGITS = 100_000


def _integer_decimal_text(number: int) -> str:
    """Print arbitrary-precision integers without Python's 4300-digit guard.

    Break the magnitude into small base-10 chunks; each conversion is below
    the host interpreter's int-to-string security threshold. No precision is
    lost and the interpreter-wide safety setting is left unchanged.
    """
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    number = abs(number)
    base = 10 ** 1000
    chunks = []
    while number:
        number, remainder = divmod(number, base)
        chunks.append(str(remainder).zfill(1000) if number else str(remainder))
    return sign + "".join(reversed(chunks))


@total_ordering
@dataclass(frozen=True, slots=True)
class BigDecimal:
    """
    Exact finite decimal.

    Value = coefficient * 10 ** exponent
    """

    coefficient: int
    exponent: int = 0

    def __post_init__(self):
        """Normalize the BigDecimal by removing trailing zeros from the coefficient."""
        if self.coefficient == 0:
            if self.exponent != 0:
                object.__setattr__(self, "exponent", 0)
            return

        coefficient = self.coefficient
        if coefficient % 10:
            # Most intermediate arithmetic results are already canonical;
            # their frozen dataclass fields need no further writes.
            return
        exponent = self.exponent

        while coefficient % 10 == 0:
            coefficient //= 10
            exponent += 1

        object.__setattr__(self, "coefficient", coefficient)
        object.__setattr__(self, "exponent", exponent)

    # ---------- construction ----------

    @classmethod
    def from_int(cls, value: int):
        """Create a BigDecimal from an integer."""
        return cls(value)

    @classmethod
    def from_string(cls, value: str):
        """Create a BigDecimal from a string representation of a decimal number."""
        d = Decimal(value)
        if not d.is_finite():
            raise ValueError("BigDecimal requires a finite decimal")

        sign, digits, exponent = d.as_tuple()

        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit

        if sign:
            coefficient = -coefficient

        return cls(coefficient, exponent)

    @classmethod
    def from_decimal(cls, value: Decimal):
        """Create a BigDecimal from a Decimal object."""
        if not value.is_finite():
            raise ValueError("BigDecimal requires a finite decimal")
        sign, digits, exponent = value.as_tuple()

        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit

        if sign:
            coefficient = -coefficient

        return cls(coefficient, exponent)

    @classmethod
    def from_float(cls, value: float):
        """Create a BigDecimal from a float."""
        # Valiance treats decimal input as an exact decimal. Python's float is
        # binary, so import its shortest displayed decimal spelling instead
        # of copying the underlying binary rounding error into the language.
        # Comparison against a native float still uses its exact binary value
        # to preserve Python's equality/hash contract.
        return cls.from_string(str(value))

    # ---------- properties ----------

    def is_zero(self):
        """Return whether this BigDecimal is zero."""
        return self.coefficient == 0

    def is_integer(self):
        """Return whether this BigDecimal represents an integer."""
        if self.is_zero():
            return True

        # Nonzero coefficients are normalized to have no trailing zeroes.
        # A negative exponent can therefore never denote an integer.
        return self.exponent >= 0

    def digits(self):
        """Return the number of digits in the coefficient of this BigDecimal."""
        return len(_integer_decimal_text(abs(self.coefficient)))

    # ---------- comparison ----------

    def _align(self, other: BigDecimal):
        """Align two BigDecimal numbers to the same exponent for comparison."""
        exponent = min(self.exponent, other.exponent)
        if max(self.exponent, other.exponent) - exponent > MAX_DECIMAL_EXPANSION_DIGITS:
            raise RuntimeError("decimal operation would expand more than 100000 digits")

        a = self.coefficient * 10 ** (self.exponent - exponent)
        b = other.coefficient * 10 ** (other.exponent - exponent)

        return a, b

    def __eq__(self, other):
        """Check if two BigDecimal numbers are equal."""
        if not isinstance(other, BigDecimal):
            return NotImplemented

        return self.coefficient == other.coefficient and self.exponent == other.exponent

    def __lt__(self, other):
        """Compare magnitudes before aligning potentially huge decimal scales."""
        if self.coefficient <= 0 < other.coefficient:
            return True
        if other.coefficient <= 0 < self.coefficient:
            return False
        if self.coefficient == 0 or other.coefficient == 0:
            return self.coefficient < other.coefficient
        if self.exponent == other.exponent:
            return self.coefficient < other.coefficient
        # Decimal magnitude is determined by coefficient digit count and
        # exponent; compare aligned coefficients only for equal magnitudes.
        left_order = self.digits() + self.exponent
        right_order = other.digits() + other.exponent
        if left_order != right_order:
            return (left_order < right_order) if self.coefficient > 0 else (left_order > right_order)
        a, b = self._align(other)
        return a < b

    # ---------- arithmetic ----------

    def __neg__(self):
        """Return the negation of this BigDecimal."""
        return BigDecimal(
            -self.coefficient,
            self.exponent,
        )

    def __add__(self, other):
        """Add two BigDecimal numbers."""
        if self.is_zero():
            return other
        if other.is_zero():
            return self
        if self.exponent == other.exponent:
            return BigDecimal(self.coefficient + other.coefficient, self.exponent)
        a, b = self._align(other)
        return BigDecimal(a + b, min(self.exponent, other.exponent))

    def __sub__(self, other):
        """Subtract two BigDecimal numbers."""
        if other.is_zero():
            return self
        if self.is_zero():
            return -other
        if self.exponent == other.exponent:
            return BigDecimal(self.coefficient - other.coefficient, self.exponent)
        a, b = self._align(other)
        return BigDecimal(a - b, min(self.exponent, other.exponent))

    def __mul__(self, other):
        """Multiply two BigDecimal numbers."""
        if self.coefficient == 0 or other.coefficient == 0:
            return BigDecimal(0)
        return BigDecimal(
            self.coefficient * other.coefficient,
            self.exponent + other.exponent,
        )

    def scaleb(self, n: int) -> BigDecimal:
        """Return this BigDecimal scaled by 10**n."""
        return BigDecimal(
            self.coefficient,
            self.exponent + n,
        )

    def floor_div(self, other: BigDecimal) -> int:
        """
        Exact floor(self / other) for finite decimals.
        """
        numerator = self.coefficient
        denominator = other.coefficient

        if denominator == 0:
            raise ZeroDivisionError()

        if numerator == 0:
            return 0
        # If the dividend has smaller magnitude, floor is 0 for like
        # signs and -1 for opposite signs, regardless of exponent size.
        if BigDecimal(abs(numerator), self.exponent) < BigDecimal(abs(denominator), other.exponent):
            return -1 if (numerator < 0) != (denominator < 0) else 0
        exponent = self.exponent - other.exponent
        if abs(exponent) > MAX_DECIMAL_EXPANSION_DIGITS:
            raise RuntimeError("decimal operation would expand more than 100000 digits")

        if exponent >= 0:
            numerator *= 10**exponent
        else:
            denominator *= 10 ** (-exponent)

        # Python-style floor division.
        return numerator // denominator

    def __mod__(self, other: BigDecimal) -> BigDecimal:
        """Return an exact remainder without unnecessarily expanding 10**N.

        Scaling the dividend can be done using modular exponentiation.  When
        the divisor has the larger scale, the dividend may already be a valid
        remainder, so avoid expanding that divisor as well.
        """
        if other.coefficient == 0:
            raise ZeroDivisionError("modulo by zero")
        if self.coefficient == 0:
            return BigDecimal(0)
        if self.exponent >= other.exponent:
            shift = self.exponent - other.exponent
            factor_mod = pow(10, shift, abs(other.coefficient))
            remainder = (self.coefficient * factor_mod) % other.coefficient
            return BigDecimal(remainder, other.exponent)
        # For a positive divisor the small positive dividend is its remainder;
        # likewise for two negative operands.  A differing sign may require a
        # genuinely enormous remainder, which is subject to the normal guard.
        if (self.coefficient > 0) == (other.coefficient > 0):
            if BigDecimal(abs(self.coefficient), self.exponent) < BigDecimal(abs(other.coefficient), other.exponent):
                return self
        dividend, divisor = self._align(other)
        return BigDecimal(dividend % divisor, self.exponent)

    # ---------- conversion ----------

    def to_int(self):
        """Return the integer value of this BigDecimal."""
        if self.exponent >= 0:
            if self.exponent > MAX_DECIMAL_EXPANSION_DIGITS:
                raise RuntimeError("integer conversion would expand more than 100000 digits")
            return self.coefficient * 10**self.exponent

        shift = -self.exponent
        if shift > self.digits():
            return -1 if self.coefficient < 0 else 0
        if shift > MAX_DECIMAL_EXPANSION_DIGITS:
            raise RuntimeError("integer conversion would expand more than 100000 digits")
        return self.coefficient // (10**shift)

    def to_decimal(self, precision=100):
        """Return the Decimal representation of this BigDecimal."""
        # Decimal(tuple) is *exact*: a context multiplication silently rounds
        # coefficients with more than `precision` significant digits.
        digits = _integer_decimal_text(abs(self.coefficient))
        exact = Decimal((int(self.coefficient < 0), tuple(map(int, digits)), self.exponent))
        if precision is None:
            return exact
        with localcontext() as ctx:
            ctx.prec = precision
            return +exact

    def __str__(self):
        """Return a string representation of this BigDecimal."""
        if self.is_zero():
            return "0"

        coefficient = _integer_decimal_text(abs(self.coefficient))
        if abs(self.exponent) > MAX_DECIMAL_EXPANSION_DIGITS:
            sign = "-" if self.coefficient < 0 else ""
            return f"{sign}{coefficient}e{self.exponent:+d}"

        if self.exponent >= 0:
            result = coefficient + ("0" * self.exponent)
        else:
            point = len(coefficient) + self.exponent

            if point <= 0:
                result = "0." + ("0" * -point) + coefficient
            else:
                result = coefficient[:point] + "." + coefficient[point:]

        if self.coefficient < 0:
            result = "-" + result

        return result

    def __format__(self, format_spec: str) -> str:
        """Format finite decimals without expanding impractically large exponents."""
        if not format_spec:
            return str(self)
        if abs(self.exponent) > MAX_DECIMAL_EXPANSION_DIGITS:
            if format_spec in {"f", "F", "g", "G", "e", "E"}:
                return str(self)
            raise RuntimeError("decimal formatting would expand more than 100000 digits")
        return format(self.to_decimal(None), format_spec)
