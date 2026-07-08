import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../app/widgets/page_header.dart';
import '../../core/api/token_store.dart';
import '../../core/offline/database.dart';
import '../auth/auth_state.dart';
import '../execution/execution_controller.dart';
import '../jobs/jobs_providers.dart';
import 'vendor_profile_provider.dart';

final pendingOutboxProvider = StreamProvider<List<OutboxEntry>>((ref) {
  final db = ref.watch(syncServiceProvider).db;
  return (db.select(
    db.outboxEntries,
  )..where((row) => row.status.equals('pending'))).watch();
});

final conflictOutboxProvider = StreamProvider<List<OutboxEntry>>((ref) {
  final db = ref.watch(syncServiceProvider).db;
  return (db.select(
    db.outboxEntries,
  )..where((row) => row.status.equals('conflict'))).watch();
});

final pendingPhotosProvider = StreamProvider<int>((ref) {
  final db = ref.watch(syncServiceProvider).db;
  return (db.select(db.pendingPhotos)
        ..where((row) => row.uploaded.equals(false)))
      .watch()
      .map((rows) => rows.length);
});

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final me = auth is Authenticated && auth.mode == LoginMode.vendor
        ? null
        : ref.watch(meProvider);
    final vendorMe = auth is Authenticated && auth.mode == LoginMode.vendor
        ? ref.watch(vendorProfileProvider)
        : null;
    final pending = ref.watch(pendingOutboxProvider).value ?? [];
    final conflicts = ref.watch(conflictOutboxProvider).value ?? [];
    final pendingPhotos = ref.watch(pendingPhotosProvider).value ?? 0;
    final queuedCount = pending.length + pendingPhotos;
    final syncedRatio = queuedCount == 0 ? 1.0 : 0.68;
    final displayName = vendorMe?.value?.name ?? me?.value?.name ?? 'Profile';
    final subtitle = vendorMe?.value != null
        ? [
            vendorMe!.value!.vendorRole ?? 'Vendor',
            vendorMe.value!.vendorName,
          ].join(' • ')
        : '${me?.value?.openJobs ?? 0} open • ${me?.value?.completedToday ?? 0} done today';

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: ListView(
          padding: const EdgeInsets.only(bottom: 96),
          children: [
            PageHeader(
              title: 'Profile',
              trailing: HeaderActions(name: displayName),
              compact: true,
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
              child: Center(
                child: _IdentityCard(name: displayName, subtitle: subtitle),
              ),
            ),
            const SizedBox(height: AppSpace.lg),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
              child: _SyncStatusCard(
                queuedCount: queuedCount,
                syncedRatio: syncedRatio,
              ),
            ),
            const SizedBox(height: AppSpace.md),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
              child: _SyncNowCard(
                onTap: () async {
                  final sync = ref.read(syncServiceProvider);
                  await sync.flushAll();
                },
              ),
            ),
            if (pending.isNotEmpty || pendingPhotos > 0) ...[
              const SizedBox(height: AppSpace.lg),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
                child: Text(
                  'Queued Actions',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
              ),
              const SizedBox(height: AppSpace.md),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
                child: Column(
                  children: [
                    if (pendingPhotos > 0)
                      const _QueueCard(
                        icon: Icons.photo_camera_outlined,
                        title: 'Site Inspection Photos',
                        subtitle: 'Pending upload',
                      ),
                    for (final entry in pending) ...[
                      if (pendingPhotos > 0 || entry != pending.first)
                        const SizedBox(height: AppSpace.sm),
                      _QueueCard(
                        icon: Icons.assignment_outlined,
                        title: entry.kind,
                        subtitle: entry.createdAt.toString(),
                      ),
                    ],
                  ],
                ),
              ),
            ],
            if (conflicts.isNotEmpty) ...[
              const SizedBox(height: AppSpace.lg),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        'Needs Review',
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: AppColors.error,
                        borderRadius: BorderRadius.circular(AppRadii.full),
                      ),
                      child: Text(
                        '${conflicts.length} REJECTED',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                              color: Colors.white,
                            ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpace.md),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
                child: Column(
                  children: [
                    for (final entry in conflicts) ...[
                      _ConflictCard(entry: entry),
                      const SizedBox(height: AppSpace.md),
                    ],
                  ],
                ),
              ),
            ],
            const SizedBox(height: AppSpace.lg),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
              child: OutlinedButton.icon(
                key: const Key('logout'),
                icon: const Icon(Icons.logout),
                label: const Text('Sign out'),
                onPressed: () =>
                    ref.read(authControllerProvider.notifier).logout(),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _IdentityCard extends StatelessWidget {
  const _IdentityCard({required this.name, required this.subtitle});

  final String name;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Stack(
          children: [
            HeaderAvatar(name: name, radius: 48, borderColor: AppColors.primary),
            Positioned(
              right: 0,
              bottom: 0,
              child: Container(
                width: 40,
                height: 40,
                decoration: const BoxDecoration(
                  color: AppColors.primary,
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.edit, color: Colors.white, size: 18),
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpace.md),
        Text(name, style: Theme.of(context).textTheme.headlineMedium),
        const SizedBox(height: AppSpace.xs),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: appMutedText(context),
              ),
        ),
      ],
    );
  }
}

class _SyncStatusCard extends StatelessWidget {
  const _SyncStatusCard({
    required this.queuedCount,
    required this.syncedRatio,
  });

  final int queuedCount;
  final double syncedRatio;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpace.md),
      decoration: BoxDecoration(
        color: appSurface(context),
        borderRadius: BorderRadius.circular(AppRadii.md),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'SYNC STATUS',
                style: Theme.of(context).textTheme.labelLarge,
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: AppColors.primarySoft,
                  borderRadius: BorderRadius.circular(AppRadii.full),
                ),
                child: Text(
                  'Offline Queue',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: AppColors.secondary,
                      ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpace.md),
          Text(
            '$queuedCount',
            key: const Key('sync-counts'),
            style: Theme.of(context).textTheme.displaySmall,
          ),
          const SizedBox(height: AppSpace.xs),
          Text(
            'Items pending upload',
            style: Theme.of(context).textTheme.bodyLarge,
          ),
          const SizedBox(height: AppSpace.md),
          ClipRRect(
            borderRadius: BorderRadius.circular(AppRadii.full),
            child: LinearProgressIndicator(
              value: syncedRatio,
              minHeight: 8,
              backgroundColor: AppColors.surfaceContainer,
              color: AppColors.primary,
            ),
          ),
          const SizedBox(height: AppSpace.sm),
          Text(
            '${(syncedRatio * 100).round()}% of local data synchronized',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _SyncNowCard extends StatelessWidget {
  const _SyncNowCard({required this.onTap});

  final Future<void> Function() onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      key: const Key('sync-now'),
      borderRadius: BorderRadius.circular(AppRadii.md),
      onTap: () {
        onTap();
      },
      child: Container(
        padding: const EdgeInsets.all(AppSpace.lg),
        decoration: BoxDecoration(
          color: AppColors.primaryContainer,
          borderRadius: BorderRadius.circular(AppRadii.md),
          boxShadow: appSoftShadow(Theme.of(context).brightness == Brightness.dark),
        ),
        child: Column(
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.18),
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.sync, color: Colors.white, size: 32),
            ),
            const SizedBox(height: AppSpace.md),
            Text(
              'Sync Now',
              style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                    color: Colors.white,
                  ),
            ),
            const SizedBox(height: AppSpace.xs),
            Text(
              'Last synced 24m ago',
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                    color: Colors.white.withValues(alpha: 0.85),
                  ),
            ),
          ],
        ),
      ),
    );
  }
}

class _QueueCard extends StatelessWidget {
  const _QueueCard({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpace.md),
      decoration: BoxDecoration(
        color: appSurface(context),
        borderRadius: BorderRadius.circular(AppRadii.md),
        border: Border.all(color: appOutline(context).withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: Theme.of(context).brightness == Brightness.dark
                  ? AppColors.darkContainer
                  : AppColors.surfaceContainer,
              borderRadius: BorderRadius.circular(AppRadii.sm),
            ),
            child: Icon(icon, color: appMutedText(context)),
          ),
          const SizedBox(width: AppSpace.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: AppSpace.xs),
                Text(subtitle, style: Theme.of(context).textTheme.bodyMedium),
              ],
            ),
          ),
          Icon(Icons.more_vert, color: appMutedText(context)),
        ],
      ),
    );
  }
}

class _ConflictCard extends ConsumerWidget {
  const _ConflictCard({required this.entry});

  final OutboxEntry entry;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Container(
      decoration: BoxDecoration(
        color: appSurface(context),
        borderRadius: BorderRadius.circular(AppRadii.md),
        border: const Border(
          left: BorderSide(color: AppColors.error, width: 4),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpace.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        entry.kind,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                      const SizedBox(height: AppSpace.xs),
                      Text(
                        entry.lastError ?? 'Rejected by the server',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                    ],
                  ),
                ),
                const Icon(Icons.error_outline, color: AppColors.error),
              ],
            ),
            const SizedBox(height: AppSpace.sm),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(AppSpace.sm),
              decoration: BoxDecoration(
                color: AppColors.errorSoft.withValues(alpha: 0.6),
                borderRadius: BorderRadius.circular(AppRadii.sm),
              ),
              child: Text(
                entry.lastError ?? 'Review with dispatch, then discard.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: AppColors.error,
                    ),
              ),
            ),
            const SizedBox(height: AppSpace.sm),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  key: Key('discard-${entry.clientRef}'),
                  onPressed: () async {
                    final db = ref.read(syncServiceProvider).db;
                    await (db.delete(
                      db.outboxEntries,
                    )..where((row) => row.seq.equals(entry.seq))).go();
                  },
                  child: const Text('Dismiss'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
