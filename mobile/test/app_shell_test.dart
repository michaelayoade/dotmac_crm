import 'package:dotmac_field/app/app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('app shell renders four-tab navigation', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: DotmacFieldApp()));
    await tester.pumpAndSettle();

    expect(find.text('Today'), findsWidgets);
    expect(find.text('Map'), findsOneWidget);
    expect(find.text('Schedule'), findsOneWidget);
    expect(find.text('Profile'), findsOneWidget);
    expect(find.byType(NavigationBar), findsOneWidget);
  });

  testWidgets('tapping a tab switches branch', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: DotmacFieldApp()));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Schedule'));
    await tester.pumpAndSettle();
    expect(find.text('Schedule'), findsWidgets);
  });
}
