import 'dart:ui';

import 'package:flutter/material.dart';

import '../theme.dart';

class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.value,
    required this.label,
    this.unit,
    this.highlighted = false,
  });

  final String value;
  final String label;
  final String? unit;
  final bool highlighted;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final valueColor = highlighted ? AppColors.primary : AppColors.secondary;
    final labelColor = appMutedText(context);

    return Container(
      height: 126,
      padding: const EdgeInsets.all(AppSpace.md),
      decoration: BoxDecoration(
        color: appSurface(context),
        borderRadius: BorderRadius.circular(AppRadii.md),
        border: Border.all(color: appOutline(context).withValues(alpha: 0.3)),
        boxShadow: appSoftShadow(isDark),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Icon(
            highlighted ? Icons.assignment_outlined : Icons.check_circle_outline,
            color: valueColor,
            size: 22,
          ),
          Text.rich(
            TextSpan(
              text: value,
              children: [
                if (unit != null)
                  TextSpan(
                    text: unit,
                    style: const TextStyle(fontSize: 13),
                  ),
              ],
            ),
            style: TextStyle(
              fontFamily: 'PlusJakartaSans',
              fontSize: 40,
              height: 1,
              fontWeight: FontWeight.w700,
              color: valueColor,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
          Text(
            label,
            style: TextStyle(
              fontFamily: 'PlusJakartaSans',
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: labelColor,
              letterSpacing: 0.3,
            ),
          ),
        ],
      ),
    );
  }
}
