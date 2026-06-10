import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/auth/auth_repository.dart' show appVersion;
import '../../features/auth/auth_state.dart';
import 'push_source.dart';

final pushSourceProvider = Provider<PushSource>((ref) => const NoopPushSource());

String get _platformName {
  if (kIsWeb) return 'android'; // web builds register as android for now
  return Platform.isIOS ? 'ios' : 'android';
}

/// Registers the device token after login and on token rotation, and turns
/// notification taps into deep-link navigations.
class PushRegistrar {
  PushRegistrar(this.ref, {required this.onDeepLink});

  final Ref ref;
  final void Function(String route) onDeepLink;

  StreamSubscription<String>? _tokenSub;
  StreamSubscription<PushMessage>? _messageSub;

  void start() {
    ref.listen<AuthState>(authControllerProvider, (previous, next) {
      if (next is Authenticated && previous is! Authenticated) {
        unawaited(registerToken());
      }
    });
    final source = ref.read(pushSourceProvider);
    _tokenSub = source.tokenRefresh.listen((_) => unawaited(registerToken()));
    _messageSub = source.messages.listen((message) {
      if (!message.fromTap) return;
      final route = routeForMessage(message.data);
      if (route != null) onDeepLink(route);
    });
  }

  Future<void> dispose() async {
    await _tokenSub?.cancel();
    await _messageSub?.cancel();
  }

  Future<bool> registerToken() async {
    if (ref.read(authControllerProvider) is! Authenticated) return false;
    final token = await ref.read(pushSourceProvider).token;
    if (token == null) return false;
    try {
      await ref.read(apiClientProvider).dio.post('/api/v1/field/devices', data: {
        'platform': _platformName,
        'fcm_token': token,
        'app_version': appVersion,
      });
      return true;
    } catch (_) {
      // Registration is retried on next login/token rotation; push being
      // down must never break the app.
      return false;
    }
  }
}

final pushRegistrarProvider = Provider<PushRegistrar>((ref) {
  throw UnimplementedError('constructed at bootstrap with a navigation callback');
});
