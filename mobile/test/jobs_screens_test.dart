import 'package:dotmac_field/app/theme.dart';
import 'package:dotmac_field/features/jobs/job_detail_screen.dart';
import 'package:dotmac_field/features/jobs/job_models.dart';
import 'package:dotmac_field/features/jobs/jobs_providers.dart';
import 'package:dotmac_field/features/jobs/widgets/job_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

JobSummary _job({String status = 'dispatched', String workType = 'install'}) =>
    JobSummary(
      id: 'wo-1',
      title: 'Install fiber — Adaeze Okafor',
      status: status,
      workType: workType,
      priority: 'normal',
      scheduledStart: DateTime.utc(2026, 6, 10, 9),
      estimatedDurationMinutes: 90,
    );

JobDetail _detail({
  String status = 'dispatched',
  JobLocation? location,
  List<Map<String, dynamic>> materialRequests = const [],
}) => JobDetail(
  job: _job(status: status),
  location:
      location ??
      const JobLocation(
        latitude: 6.43,
        longitude: 3.42,
        addressText: '12 Admiralty Way',
        source: 'geocoded',
      ),
  customer: const JobCustomer(
    name: 'Adaeze Okafor',
    phone: '+2348012345678',
    servicePlan: '100 Mbps',
  ),
  ticketRef: 'TCK-1001',
  materialRequests: materialRequests,
);

Widget _wrap(Widget child, {List<Override> overrides = const []}) =>
    ProviderScope(
      overrides: overrides,
      child: MaterialApp(theme: lightTheme, home: child),
    );

void main() {
  testWidgets('job card shows work-type color bar and status dot', (
    tester,
  ) async {
    await tester.pumpWidget(_wrap(JobCard(job: _job())));

    expect(find.text('INSTALL'), findsOneWidget);
    expect(find.text('Install fiber — Adaeze Okafor'), findsOneWidget);
    expect(find.text('dispatched'), findsOneWidget);
    expect(find.text('~90 min'), findsOneWidget);

    final bar = tester
        .widgetList<Container>(find.byType(Container))
        .firstWhere(
          (c) =>
              c.constraints?.maxWidth == 5 ||
              c.color == AppColors.workType('install'),
          orElse: () => tester.widget<Container>(find.byType(Container).first),
        );
    expect(bar.color, AppColors.workType('install'));
  });

  group('action bar shows exactly one primary action per status', () {
    for (final (status, expected) in [
      ('scheduled', 'Accept job'),
      ('dispatched', 'Start job'),
      ('in_progress', 'Complete job'),
    ]) {
      testWidgets(status, (tester) async {
        final detail = _detail(status: status);
        await tester.pumpWidget(
          _wrap(
            const SizedBox(),
            overrides: [
              jobDetailProvider('wo-1').overrideWith((ref) async => detail),
            ],
          ),
        );
        await tester.pumpWidget(
          _wrap(
            const JobDetailScreen(jobId: 'wo-1'),
            overrides: [
              jobDetailProvider('wo-1').overrideWith((ref) async => detail),
            ],
          ),
        );
        await tester.pumpAndSettle();

        expect(find.byKey(const Key('primary-action')), findsOneWidget);
        expect(find.text(expected), findsOneWidget);
      });
    }

    testWidgets('completed jobs have no primary action', (tester) async {
      final detail = _detail(status: 'completed');
      await tester.pumpWidget(
        _wrap(
          const JobDetailScreen(jobId: 'wo-1'),
          overrides: [
            jobDetailProvider('wo-1').overrideWith((ref) async => detail),
          ],
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('primary-action')), findsNothing);
    });
  });

  testWidgets('navigate button launches geo uri from coordinates', (
    tester,
  ) async {
    final launched = <Uri>[];
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider('wo-1').overrideWith((ref) async => _detail()),
          uriLauncherProvider.overrideWithValue((uri) async {
            launched.add(uri);
            return true;
          }),
        ],
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('navigate-button')));
    expect(launched.single.toString(), 'geo:6.43,3.42?q=6.43,3.42');
  });

  testWidgets('address-only location falls back to maps text search', (
    tester,
  ) async {
    final launched = <Uri>[];
    const location = JobLocation(
      addressText: '12 Admiralty Way, Lekki',
      source: 'address_only',
    );
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider(
            'wo-1',
          ).overrideWith((ref) async => _detail(location: location)),
          uriLauncherProvider.overrideWithValue((uri) async {
            launched.add(uri);
            return true;
          }),
        ],
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('navigate-button')));
    expect(launched.single.toString(), contains('q=12%20Admiralty%20Way'));
  });

  test('maps uri is null when nothing is known', () {
    const location = JobLocation(source: 'none');
    expect(location.mapsUri, isNull);
  });

  test('job detail accepts paginated notes and ignores malformed entries', () {
    final detail = JobDetail.fromJson({
      'job': {
        'id': 'wo-1',
        'title': 'Install',
        'status': 'dispatched',
        'work_type': 'install',
        'priority': 'normal',
      },
      'location': {'source': 'none'},
      'notes': {
        'items': [
          {'text': 'Stored note returned as text'},
          null,
          'unexpected',
        ],
      },
      'materials': null,
    });

    expect(detail.notes, hasLength(1));
    expect(detail.notes.single['text'], 'Stored note returned as text');
  });

  testWidgets('job detail renders notes returned with alternate body key', (
    tester,
  ) async {
    final detail = JobDetail(
      job: _job(),
      location: const JobLocation(source: 'none'),
      notes: const [
        {
          'text': 'Stored note returned as text',
          'author_name': 'Adaeze Okafor',
        },
      ],
    );
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider('wo-1').overrideWith((ref) async => detail),
        ],
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Stored note returned as text'), findsOneWidget);
    expect(find.text('Adaeze Okafor'), findsOneWidget);
  });

  testWidgets('call button dials the customer', (tester) async {
    final launched = <Uri>[];
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider('wo-1').overrideWith((ref) async => _detail()),
          uriLauncherProvider.overrideWithValue((uri) async {
            launched.add(uri);
            return true;
          }),
        ],
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('call-button')));
    expect(launched.single.scheme, 'tel');
  });

  testWidgets('job detail shows linked material requests', (tester) async {
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider('wo-1').overrideWith(
            (ref) async => _detail(
              materialRequests: const [
                {
                  'id': 'mr-1',
                  'number': 'MR-1001',
                  'status': 'submitted',
                  'items': [
                    {'id': 'item-1'},
                  ],
                },
              ],
            ),
          ),
        ],
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Material requests'), findsOneWidget);
    expect(find.text('MR-1001'), findsOneWidget);
    expect(find.text('submitted · 1 item'), findsOneWidget);
  });

  testWidgets('technician can open add note composer from job detail', (
    tester,
  ) async {
    await tester.pumpWidget(
      _wrap(
        const JobDetailScreen(jobId: 'wo-1'),
        overrides: [
          jobDetailProvider('wo-1').overrideWith((ref) async => _detail()),
        ],
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('add-note-action')));
    await tester.pumpAndSettle();

    expect(find.text('Add note'), findsWidgets);
    expect(find.byKey(const Key('note-body-field')), findsOneWidget);
    expect(find.byKey(const Key('internal-note-checkbox')), findsOneWidget);
    expect(find.text('Visible to staff only'), findsOneWidget);
    expect(find.byKey(const Key('save-note-action')), findsOneWidget);
  });
}
