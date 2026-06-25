import 'package:latlong2/latlong.dart';

const defaultMapCenter = LatLng(6.5244, 3.3792);

bool isValidMapCoordinate(double? latitude, double? longitude) {
  return latitude != null &&
      longitude != null &&
      latitude.isFinite &&
      longitude.isFinite &&
      latitude >= -90 &&
      latitude <= 90 &&
      longitude >= -180 &&
      longitude <= 180;
}

LatLng? safeLatLng(double? latitude, double? longitude) {
  if (!isValidMapCoordinate(latitude, longitude)) return null;
  return LatLng(latitude!, longitude!);
}
