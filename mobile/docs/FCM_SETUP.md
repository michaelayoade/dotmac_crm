# Enabling Push Notifications (FCM)

The app ships with push **registration wired but transport inert** —
`pushSourceProvider` (in `lib/core/push/push_registrar.dart`) returns
`NoopPushSource`, and the backend `POST /api/v1/field/devices` happily records a
token that never receives anything. Everything downstream (deep-link routing in
`PushRegistrar`, the backend FCM sender in `app/services/push.py`) is already
built and tested.

This is **not** landed in code because `firebase_messaging` + the
`google-services` Gradle plugin will **fail the release build** without the
Firebase config files, and those can only come from *your* Firebase project.
Follow the steps below to activate (~15 min).

App identifiers (already set): Android `io.dotmac.dotmac_field`, iOS bundle id —
match it in Firebase.

## Steps

1. **Create / pick a Firebase project**, then add an Android app
   (`io.dotmac.dotmac_field`) and an iOS app (matching bundle id).

2. **Generate config** with the FlutterFire CLI — this drops
   `lib/firebase_options.dart`, `android/app/google-services.json`, and
   `ios/Runner/GoogleService-Info.plist`:
   ```bash
   dart pub global activate flutterfire_cli
   flutterfire configure
   ```

3. **Add the dependencies:**
   ```bash
   flutter pub add firebase_core firebase_messaging
   ```

4. **Create `lib/core/push/fcm_push_source.dart`** with the implementation
   below (it satisfies the existing `PushSource` interface — no other change to
   the registrar needed):

   ```dart
   import 'dart:async';

   import 'package:firebase_messaging/firebase_messaging.dart';

   import 'push_source.dart';

   /// Real FCM transport. Satisfies [PushSource]; covers Android + iOS (APNs
   /// relay). Only constructed when Firebase is configured (see main.dart).
   class FcmPushSource implements PushSource {
     FcmPushSource({FirebaseMessaging? messaging})
         : _fm = messaging ?? FirebaseMessaging.instance {
       FirebaseMessaging.onMessage.listen(_emit);
       FirebaseMessaging.onMessageOpenedApp.listen((m) => _emit(m, fromTap: true));
     }

     final FirebaseMessaging _fm;
     final _messages = StreamController<PushMessage>.broadcast();

     Future<void> _ensurePermission() =>
         _fm.requestPermission(alert: true, badge: true, sound: true);

     @override
     Future<String?> get token async {
       await _ensurePermission();
       return _fm.getToken();
     }

     @override
     Stream<String> get tokenRefresh => _fm.onTokenRefresh;

     @override
     Stream<PushMessage> get messages => _messages.stream;

     /// Surface a tap that cold-launched the app from a terminated state.
     Future<void> handleInitialMessage() async {
       final initial = await _fm.getInitialMessage();
       if (initial != null) _emit(initial, fromTap: true);
     }

     void _emit(RemoteMessage m, {bool fromTap = false}) {
       _messages.add(PushMessage(
         title: m.notification?.title,
         body: m.notification?.body,
         data: m.data.map((k, v) => MapEntry(k, '$v')),
         fromTap: fromTap,
       ));
     }

     void dispose() => _messages.close();
   }
   ```

5. **Wire it in `lib/main.dart`** — initialize Firebase and override the
   provider (add the imports for `firebase_core`, `firebase_options.dart`,
   `fcm_push_source.dart`, and `push_registrar.dart`):

   ```dart
   // after WidgetsFlutterBinding.ensureInitialized():
   await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
   final fcm = FcmPushSource();
   await fcm.handleInitialMessage();

   // add to the ProviderScope overrides list:
   pushSourceProvider.overrideWithValue(fcm),
   ```

6. **iOS only:** upload your APNs auth key (`.p8`) to Firebase →
   Project Settings → Cloud Messaging, and enable Push Notifications +
   Background Modes (remote notifications) capabilities in Xcode.

7. **Backend:** set `FCM_SERVICE_ACCOUNT_JSON` (and optionally `FCM_PROJECT_ID`)
   on the API — see `app/services/push.py`. Until set, sends are recorded as
   failed deliveries and nothing is delivered.

## Verifying

- `flutter run`, log in, confirm a `DeviceToken` row is created server-side.
- Assign a work order to the logged-in tech → `queue_work_order_assignment_push`
  fires → device receives "New job assigned" → tapping it deep-links to
  `/jobs/{id}` (already handled by `routeForMessage` in `push_source.dart`).
