import 'package:dotmac_field/features/schedule/schedule_providers.dart';
import 'package:dotmac_field/features/schedule/schedule_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

ScheduleEntry _entry(String type, DateTime start, {String title = 'Entry', String ref = 'r1'}) =>
    ScheduleEntry(type: type, startAt: start, title: title, referenceId: ref);

void main() {
  test('groupByDay sorts entries and groups by local day', () {
    final day1 = DateTime.utc(2026, 6, 10, 9);
    final day2 = DateTime.utc(2026, 6, 11, 8);
    final groups = groupByDay([
      _entry('job', day2, title: 'B'),
      _entry('shift', day1, title: 'A2'),
      _entry('job', day1.subtract(const Duration(hours: 2)), title: 'A1'),
    ]);

    expect(groups.length, 2);
    expect(groups.first.$2.map((e) => e.title), ['A1', 'A2']);
    expect(groups.last.$2.single.title, 'B');
  });

  Widget app(List<ScheduleEntry> entries) => ProviderScope(
        overrides: [scheduleProvider.overrideWith((ref) async => entries)],
        child: const MaterialApp(home: ScheduleScreen()),
      );

  testWidgets('renders day headers and entries', (tester) async {
    await tester.pumpWidget(app([
      _entry('shift', DateTime.now().add(const Duration(hours: 1)), title: 'Morning shift'),
      _entry('job', DateTime.now().add(const Duration(hours: 3)), title: 'Install fiber', ref: 'wo-1'),
    ]));
    await tester.pumpAndSettle();

    expect(find.text('Morning shift'), findsOneWidget);
    expect(find.text('Install fiber'), findsOneWidget);
  });

  testWidgets('empty schedule shows the calm empty state', (tester) async {
    await tester.pumpWidget(app([]));
    await tester.pumpAndSettle();
    expect(find.textContaining('Nothing scheduled'), findsOneWidget);
  });

  testWidgets('only job entries are tappable', (tester) async {
    await tester.pumpWidget(app([
      _entry('availability', DateTime.now(), title: 'Training'),
    ]));
    await tester.pumpAndSettle();
    final tile = tester.widget<ListTile>(find.byType(ListTile));
    expect(tile.onTap, isNull);
  });
}
