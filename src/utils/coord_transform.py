from __future__ import annotations

import math


PI = math.pi
A = 6378245.0
EE = 0.00669342162296594323


def _out_of_china(lon: float, lat: float) -> bool:
    return lon < 72.004 or lon > 137.8347 or lat < 0.8293 or lat > 55.8271


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * PI) + 320.0 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    """将 WGS84 坐标转换为 GCJ-02 坐标。"""
    if _out_of_china(lon, lat):
        return lon, lat

    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * PI
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)

    d_lat = (d_lat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * PI)
    d_lon = (d_lon * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * PI)

    mg_lat = lat + d_lat
    mg_lon = lon + d_lon
    return mg_lon, mg_lat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """将 GCJ-02 坐标近似反算为 WGS84 坐标。"""
    if _out_of_china(lon, lat):
        return lon, lat

    guessed_lon, guessed_lat = lon, lat
    for _ in range(2):
        trans_lon, trans_lat = wgs84_to_gcj02(guessed_lon, guessed_lat)
        guessed_lon -= (trans_lon - lon)
        guessed_lat -= (trans_lat - lat)
    return guessed_lon, guessed_lat


def batch_wgs84_to_gcj02(points: list[list[float]]) -> list[list[float]]:
    converted: list[list[float]] = []
    for lon, lat in points:
        c_lon, c_lat = wgs84_to_gcj02(float(lon), float(lat))
        converted.append([round(c_lon, 6), round(c_lat, 6)])
    return converted


def batch_gcj02_to_wgs84(points: list[list[float]]) -> list[list[float]]:
    converted: list[list[float]] = []
    for lon, lat in points:
        c_lon, c_lat = gcj02_to_wgs84(float(lon), float(lat))
        converted.append([round(c_lon, 6), round(c_lat, 6)])
    return converted
