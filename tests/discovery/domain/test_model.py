import pytest

from haatly.discovery.public import (
    InvalidLocationError,
    Point,
    PostOffice,
    centroids,
    check_pin,
    rounded_km,
)

NON_ASCII_DIGITS = tuple(
    "".join(chr(zero + int(digit)) for digit in "560001") for zero in (0xFF10, 0x0660, 0x0966)
)


@pytest.mark.parametrize("raw", ["560001", " 560034 ", "110001"])
def test_six_digit_pins_not_starting_with_zero_are_accepted(raw: str) -> None:
    assert check_pin(raw) == raw.strip()


@pytest.mark.parametrize(
    "raw", ["", "56000", "5600011", "060001", "56OO01", *NON_ASCII_DIGITS, "560 01"]
)
def test_anything_else_is_not_a_pin(raw: str) -> None:
    with pytest.raises(InvalidLocationError):
        check_pin(raw)


@pytest.mark.parametrize(
    ("latitude", "longitude"), [(0.0, 0.0), (51.5, -0.12), (5.9, 77.0), (20.0, 98.1)]
)
def test_points_must_be_inside_india(latitude: float, longitude: float) -> None:
    with pytest.raises(InvalidLocationError):
        Point(latitude, longitude)


def office(pin: str, latitude: float | None, longitude: float | None) -> PostOffice:
    return PostOffice(pin, "District", "State", latitude, longitude)


def test_a_centroid_averages_the_located_offices_inside_india() -> None:
    [pin] = centroids(
        [
            office("560001", 12.98, 77.59),
            office("560001", 12.97, 77.58),
            office("560001", 0.0, 0.0),
            office("560001", None, 77.5),
        ]
    )
    assert pin.pin_code == "560001"
    assert pin.centroid.latitude == pytest.approx(12.975)
    assert pin.centroid.longitude == pytest.approx(77.585)


def test_a_pin_without_any_located_office_is_left_out() -> None:
    pins = centroids([office("560095", None, None), office("110001", 28.63, 77.22)])
    assert [pin.pin_code for pin in pins] == ["110001"]


def test_distances_are_reported_to_a_tenth_of_a_kilometre() -> None:
    assert rounded_km(0) == 0.0
    assert rounded_km(1249.9) == 1.2
    assert rounded_km(1250.1) == 1.3
    assert rounded_km(49_999) == 50.0
