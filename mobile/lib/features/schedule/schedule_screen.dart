import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../app/theme.dart';
import 'schedule_providers.dart';

class ScheduleScreen extends ConsumerWidget {
  const ScheduleScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final schedule = ref.watch(scheduleProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Schedule')),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(scheduleProvider),
        child: schedule.when(
          data: (data) {
            final entries = data.entries;
            if (entries.isEmpty) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: [
                  if (data.fromCache)
                    const Padding(padding: EdgeInsets.all(16), child: _OfflineBanner()),
                  const SizedBox(height: 160),
                  const Center(child: Text('Nothing scheduled this week — enjoy the quiet')),
                ],
              );
            }
            final days = groupByDay(entries);
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                if (data.fromCache) const _OfflineBanner(),
                for (final (day, dayEntries) in days) ...[
                  Padding(
                    padding: const EdgeInsets.only(top: 8, bottom: 8),
                    child: Text(
                      DateFormat('EEEE, d MMM').format(day),
                      style: Theme.of(context)
                          .textTheme
                          .titleSmall
                          ?.copyWith(fontWeight: FontWeight.w700, letterSpacing: 0.5),
                    ),
                  ),
                  for (final entry in dayEntries) _ScheduleTile(entry: entry),
                ],
              ],
            );
          },
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (_, _) => ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: const [
              SizedBox(height: 160),
              Center(child: Text('Could not load your schedule — pull to retry')),
            ],
          ),
        ),
      ),
    );
  }
}

class _ScheduleTile extends ConsumerWidget {
  const _ScheduleTile({required this.entry});

  final ScheduleEntry entry;

  static const _icons = {
    'shift': Icons.schedule_outlined,
    'availability': Icons.event_busy_outlined,
    'job': Icons.assignment_outlined,
  };

  Color _color(BuildContext context) => switch (entry.type) {
        'availability' => const Color(0xFFF59E0B),
        'shift' => const Color(0xFF64748B),
        _ => AppColors.primary,
      };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final time = DateFormat.Hm().format(entry.startAt.toLocal());
    final end = entry.endAt != null ? '–${DateFormat.Hm().format(entry.endAt!.toLocal())}' : '';
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: Icon(_icons[entry.type] ?? Icons.event, color: _color(context)),
        title: Text(entry.title),
        subtitle: Text('$time$end · ${entry.type}'),
        onTap: entry.type == 'job' ? () => context.push('/jobs/${entry.referenceId}') : null,
        trailing: entry.type == 'job' ? const Icon(Icons.chevron_right) : null,
      ),
    );
  }
}

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        key: const Key('schedule-offline-banner'),
        children: [
          const Icon(Icons.cloud_off_outlined, size: 16),
          const SizedBox(width: 8),
          Text('Offline — showing saved schedule', style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}
