import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../theme.dart';

class PageHeader extends StatelessWidget {
  const PageHeader({
    super.key,
    required this.title,
    this.eyebrow,
    this.subtitle,
    this.trailing,
    this.compact = false,
  });

  final String title;
  final String? eyebrow;
  final String? subtitle;
  final Widget? trailing;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpace.page,
        compact ? AppSpace.md : AppSpace.lg,
        AppSpace.page,
        AppSpace.md,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (eyebrow != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: AppSpace.xs),
                    child: Text(
                      eyebrow!,
                      style: theme.textTheme.labelLarge?.copyWith(
                        color: AppColors.primary,
                      ),
                    ),
                  ),
                Text(title, style: theme.textTheme.headlineSmall),
                if (subtitle != null) ...[
                  const SizedBox(height: AppSpace.xs),
                  Text(subtitle!, style: theme.textTheme.bodyMedium),
                ],
              ],
            ),
          ),
          if (trailing != null) ...[
            const SizedBox(width: AppSpace.md),
            trailing!,
          ],
        ],
      ),
    );
  }
}

class HeaderAvatar extends StatelessWidget {
  const HeaderAvatar({
    super.key,
    this.name,
    this.radius = 20,
    this.borderColor,
  });

  final String? name;
  final double radius;
  final Color? borderColor;

  String get initials {
    final value = name?.trim();
    if (value == null || value.isEmpty) return '?';
    final parts = value.split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.length == 1) return parts.first.substring(0, 1).toUpperCase();
    return (parts.first.substring(0, 1) + parts.last.substring(0, 1))
        .toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: radius * 2,
      height: radius * 2,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        border: Border.all(
          color: borderColor ?? AppColors.primarySoft,
          width: 2,
        ),
        gradient: const LinearGradient(
          colors: [AppColors.primaryFixed, AppColors.primarySoft],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
      alignment: Alignment.center,
      child: Text(
        initials,
        style: Theme.of(context).textTheme.titleSmall?.copyWith(
              color: AppColors.primary,
            ),
      ),
    );
  }
}

class HeaderActions extends StatelessWidget {
  const HeaderActions({super.key, this.name});

  final String? name;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: appSurface(context),
            borderRadius: BorderRadius.circular(AppRadii.full),
          ),
          child: IconButton(
            onPressed: () {},
            icon: const Icon(Icons.notifications_none_rounded),
            tooltip: 'Notifications',
          ),
        ),
        const SizedBox(width: AppSpace.sm),
        GestureDetector(
          onTap: () => context.go('/profile'),
          behavior: HitTestBehavior.opaque,
          child: HeaderAvatar(name: name),
        ),
      ],
    );
  }
}
