import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../../app/theme.dart';
import '../../../app/widgets/status_pill.dart';
import '../job_models.dart';

class JobCard extends StatelessWidget {
  const JobCard({super.key, required this.job, this.onTap});

  final JobSummary job;
  final VoidCallback? onTap;

  String get _window {
    final start = job.scheduledStart?.toLocal();
    final end = job.scheduledEnd?.toLocal();
    if (start == null) return 'No time set';
    final format = DateFormat.Hm();
    return end == null
        ? format.format(start)
        : '${format.format(start)}-${format.format(end)}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final statusColor = AppColors.status(job.status);
    final duration = job.estimatedDurationMinutes;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: appSurface(context),
        borderRadius: BorderRadius.circular(AppRadii.md),
        border: Border.all(color: appOutline(context).withValues(alpha: 0.3)),
        boxShadow: appSoftShadow(isDark),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadii.md),
          onTap: onTap,
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(
                  width: 5,
                  decoration: BoxDecoration(
                    color: statusColor,
                    borderRadius: const BorderRadius.horizontal(
                      left: Radius.circular(AppRadii.md),
                    ),
                  ),
                ),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(18, 16, 16, 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(
                              Icons.history,
                              size: 18,
                              color: appMutedText(context),
                            ),
                            const SizedBox(width: 8),
                            Text(
                              _window,
                              style: theme.textTheme.titleSmall?.copyWith(
                                fontFeatures: const [
                                  FontFeature.tabularFigures(),
                                ],
                              ),
                            ),
                            const Spacer(),
                            StatusPill(job.status),
                          ],
                        ),
                        const SizedBox(height: AppSpace.md),
                        Text(
                          job.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.headlineSmall,
                        ),
                        if ((job.description ?? '').isNotEmpty) ...[
                          const SizedBox(height: AppSpace.xs),
                          Text(
                            job.description!,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.bodyMedium,
                          ),
                        ],
                        const SizedBox(height: AppSpace.md),
                        Divider(height: 1, color: appOutline(context)),
                        const SizedBox(height: AppSpace.md),
                        Row(
                          children: [
                            _Meta(
                              icon: Icons.build_outlined,
                              label: _titleCase(job.workType),
                            ),
                            if (duration != null) ...[
                              const SizedBox(width: AppSpace.md),
                              _Meta(
                                icon: Icons.timelapse_outlined,
                                label: '~$duration min',
                              ),
                            ],
                            const Spacer(),
                            Text(
                              'Open →',
                              style: theme.textTheme.titleSmall?.copyWith(
                                color: AppColors.primary,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

String _titleCase(String s) => s.isEmpty
    ? s
    : s
        .split('_')
        .map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}')
        .join(' ');

class _Meta extends StatelessWidget {
  const _Meta({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    final color = appMutedText(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 18, color: color),
        const SizedBox(width: 8),
        Text(
          label,
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: color,
              ),
        ),
      ],
    );
  }
}
