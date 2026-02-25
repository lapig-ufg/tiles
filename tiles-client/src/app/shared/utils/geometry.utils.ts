import { Feature } from 'ol';
import { Geometry, Point, MultiPoint, Polygon, MultiPolygon, LineString, MultiLineString, GeometryCollection } from 'ol/geom';
import { GeoJSON } from 'ol/format';
import { getArea, getLength } from 'ol/sphere';
import { Style, Stroke, Fill, Circle as CircleStyle } from 'ol/style';
import { transformExtent } from 'ol/proj';
import { Feature as GeoJsonFeature, Geometry as GeoJsonGeometry } from 'geojson';

export interface RepresentativePoint {
    lat: number;
    lon: number;
}

export interface GeometryInfo {
    type: string;
    representativePoint: RepresentativePoint;
    areaHa: number | null;
    perimeterKm: number | null;
    lengthKm: number | null;
    bbox: [number, number, number, number] | null;
}

/**
 * Calcula o ponto representativo de uma Feature OL.
 * - Point/MultiPoint → coordenadas diretas
 * - Polygon/MultiPolygon → getInteriorPoint() (garante ponto dentro)
 * - LineString/MultiLineString → ponto médio
 * - GeometryCollection → first geometry fallback
 */
export function computeRepresentativePoint(feature: Feature<Geometry>): RepresentativePoint | null {
    const geom = feature.getGeometry();
    if (!geom) return null;

    const coords = getRepresentativeCoords(geom);
    if (!coords) return null;

    return { lon: coords[0], lat: coords[1] };
}

function getRepresentativeCoords(geom: Geometry): number[] | null {
    if (geom instanceof Point) {
        return geom.getCoordinates();
    }
    if (geom instanceof MultiPoint) {
        return geom.getPoint(0).getCoordinates();
    }
    if (geom instanceof Polygon) {
        return geom.getInteriorPoint().getCoordinates();
    }
    if (geom instanceof MultiPolygon) {
        // Usa o maior polígono
        const polygons = geom.getPolygons();
        let largest = polygons[0];
        let maxArea = 0;
        for (const p of polygons) {
            const a = Math.abs(getArea(p));
            if (a > maxArea) {
                maxArea = a;
                largest = p;
            }
        }
        return largest.getInteriorPoint().getCoordinates();
    }
    if (geom instanceof LineString) {
        return geom.getCoordinateAt(0.5);
    }
    if (geom instanceof MultiLineString) {
        const lines = geom.getLineStrings();
        let longest = lines[0];
        let maxLen = 0;
        for (const l of lines) {
            const len = getLength(l);
            if (len > maxLen) {
                maxLen = len;
                longest = l;
            }
        }
        return longest.getCoordinateAt(0.5);
    }
    if (geom instanceof GeometryCollection) {
        const geometries = geom.getGeometries();
        if (geometries.length > 0) {
            return getRepresentativeCoords(geometries[0]);
        }
    }
    return null;
}

/**
 * Calcula informações geométricas de uma Feature OL (geometria em EPSG:3857).
 */
export function computeGeometryInfo(feature: Feature<Geometry>): GeometryInfo | null {
    const geom = feature.getGeometry();
    if (!geom) return null;

    const repPoint = computeRepresentativePoint(feature);
    if (!repPoint) return null;

    let areaHa: number | null = null;
    let perimeterKm: number | null = null;
    let lengthKm: number | null = null;

    if (geom instanceof Polygon || geom instanceof MultiPolygon) {
        const areaM2 = Math.abs(getArea(geom, { projection: 'EPSG:3857' }));
        areaHa = areaM2 / 10000;
        perimeterKm = getLength(geom, { projection: 'EPSG:3857' }) / 1000;
    }

    if (geom instanceof LineString || geom instanceof MultiLineString) {
        lengthKm = getLength(geom, { projection: 'EPSG:3857' }) / 1000;
    }

    const extent3857 = geom.getExtent();
    const extent4326 = transformExtent(extent3857, 'EPSG:3857', 'EPSG:4326');
    const bbox: [number, number, number, number] = [extent4326[0], extent4326[1], extent4326[2], extent4326[3]];

    return {
        type: geom.getType(),
        representativePoint: repPoint,
        areaHa,
        perimeterKm,
        lengthKm,
        bbox,
    };
}

/**
 * Converte uma GeoJSON Feature para uma OL Feature com reprojeção 4326→3857.
 */
export function geoJsonFeatureToOl(geoJsonFeature: GeoJsonFeature<GeoJsonGeometry>): Feature<Geometry> {
    const format = new GeoJSON();
    return format.readFeature(geoJsonFeature, {
        dataProjection: 'EPSG:4326',
        featureProjection: 'EPSG:3857',
    }) as Feature<Geometry>;
}

/**
 * Style para preview de geometria no mapa (com preenchimento).
 */
export function createGeometryStyle(): Style {
    return new Style({
        stroke: new Stroke({
            color: 'rgba(30, 100, 230, 0.9)',
            width: 2,
        }),
        fill: new Fill({
            color: 'rgba(30, 100, 230, 0.15)',
        }),
        image: new CircleStyle({
            radius: 6,
            fill: new Fill({ color: 'rgba(30, 100, 230, 0.8)' }),
            stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
    });
}

/**
 * Style sem preenchimento para geometrias no grid de imagens.
 * Apenas contorno (Stroke) para não obstruir a visualização das imagens.
 */
export function createGeometryStyleNoFill(): Style {
    return new Style({
        stroke: new Stroke({
            color: 'rgba(30, 100, 230, 0.9)',
            width: 2,
        }),
        image: new CircleStyle({
            radius: 6,
            fill: new Fill({ color: 'rgba(30, 100, 230, 0.8)' }),
            stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
    });
}
