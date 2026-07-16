"""Exact finite decimal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import total_ordering


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
            object.__setattr__(self, "exponent", 0)
            return

        coefficient = self.coefficient
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
        # Compatibility mode.
        return cls.from_string(str(value))

    # ---------- properties ----------

    def is_zero(self):
        """Return whether this BigDecimal is zero."""
        return self.coefficient == 0

    def is_integer(self):
        """Return whether this BigDecimal represents an integer."""
        if self.is_zero():
            return True

        if self.exponent >= 0:
            return True

        return self.coefficient % (10 ** (-self.exponent)) == 0

    def digits(self):
        """Return the number of digits in the coefficient of this BigDecimal."""
        return len(str(abs(self.coefficient)))

    # ---------- comparison ----------

    def _align(self, other: BigDecimal):
        """Align two BigDecimal numbers to the same exponent for comparison."""
        exponent = min(self.exponent, other.exponent)

        a = self.coefficient * 10 ** (self.exponent - exponent)
        b = other.coefficient * 10 ** (other.exponent - exponent)

        return a, b

    def __eq__(self, other):
        """Check if two BigDecimal numbers are equal."""
        if not isinstance(other, BigDecimal):
            return NotImplemented

        return self.coefficient == other.coefficient and self.exponent == other.exponent

    def __lt__(self, other):
        """Check if this BigDecimal is less than another."""
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
        a, b = self._align(other)
        exponent = min(self.exponent, other.exponent)

        return BigDecimal(
            a + b,
            exponent,
        )

    def __sub__(self, other):
        """Subtract two BigDecimal numbers."""
        a, b = self._align(other)
        exponent = min(self.exponent, other.exponent)

        return BigDecimal(
            a - b,
            exponent,
        )

    def __mul__(self, other):
        """Multiply two BigDecimal numbers."""
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

        exponent = self.exponent - other.exponent

        if exponent >= 0:
            numerator *= 10**exponent
        else:
            denominator *= 10 ** (-exponent)

        # Python-style floor division.
        return numerator // denominator

    def __mod__(self, other: BigDecimal) -> BigDecimal:
        """Return the modulus of two BigDecimal numbers."""
        quotient = self.floor_div(other)

        return self - (other * BigDecimal.from_int(quotient))

    # ---------- conversion ----------

    def to_int(self):
        """Return the integer value of this BigDecimal."""
        if self.exponent >= 0:
            return self.coefficient * 10**self.exponent

        return self.coefficient // (10**-self.exponent)

    def to_decimal(self, precision=100):
        """Return the Decimal representation of this BigDecimal."""
        with localcontext() as ctx:
            ctx.prec = precision
            return Decimal(self.coefficient) * (Decimal(10) ** self.exponent)

    def __str__(self):
        """Return a string representation of this BigDecimal."""
        if self.is_zero():
            return "0"

        coefficient = str(abs(self.coefficient))

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
        """Return a formatted string representation of this number."""
        return format(self.to_decimal(), format_spec)
