import 'package:dotmac_field/app/app.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _AuthedController extends AuthController {
  @override
  AuthState build() => const Authenticated(LoginMode.staff);
}

Widget _app({bool authenticated = true}) {
  return ProviderScope(
    overrides: [
      if (authenticated) authControllerProvider.overrideWith(_AuthedController.new),
    ],
    child: const DotmacFieldApp(),
  );
}

void main() {
  testWidgets('unauthenticated users land on the login screen', (tester) async {
    await tester.pumpWidget(_app(authenticated: false));
    await tester.pumpAndSettle();

    expect(find.text('DotMac Field'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byType(NavigationBar), findsNothing);
  });

  testWidgets('authenticated shell renders four-tab navigation', (tester) async {
    await tester.pumpWidget(_app());
    await tester.pumpAndSettle();

    expect(find.text('Today'), findsWidgets);
    expect(find.text('Map'), findsOneWidget);
    expect(find.text('Schedule'), findsOneWidget);
    expect(find.text('Profile'), findsOneWidget);
    expect(find.byType(NavigationBar), findsOneWidget);
  });

  testWidgets('tapping a tab switches branch', (tester) async {
    await tester.pumpWidget(_app());
    await tester.pumpAndSettle();

    await tester.tap(find.text('Schedule'));
    await tester.pumpAndSettle();
    expect(find.text('Schedule'), findsWidgets);
  });
}
