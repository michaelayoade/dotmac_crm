/// GPS abstraction. The device implementation lands with the geolocator
/// plugin at device-testing time; everything upstream depends only on this
/// interface so transitions and capture flows are testable headless.
typedef GeoPoint = ({double latitude, double longitude});

abstract class LocationSource {
  /// Best-effort current position; null when unavailable/denied.
  Future<GeoPoint?> current();
}

class UnavailableLocation implements LocationSource {
  const UnavailableLocation();

  @override
  Future<GeoPoint?> current() async => null;
}

class FakeLocation implements LocationSource {
  FakeLocation(this.point);

  GeoPoint? point;

  @override
  Future<GeoPoint?> current() async => point;
}
