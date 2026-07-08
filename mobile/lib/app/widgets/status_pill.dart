import 'package:flutter/material.dart';

import '../theme.dart';

/// Status shown three ways at once — dot + colour + label — so it survives
/// sunlight and colour-blindness. Colour comes from the shared status ramp.
class StatusPill extends StatelessWidget {
  const StatusPill(this.status, {super.key, this.compact = false});

  final String status;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.status(status);
    final label = AppColors.statusLabel(status);
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 10 : 14,
        vertical: compact ? 6 : 8,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(AppRadii.full),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            label.toUpperCase(),
            style: const TextStyle(
              fontFamily: 'PlusJakartaSans',
              fontSize: 11,
              fontWeight: FontWeight.w600,
              letterSpacing: 1,
            ).copyWith(color: color),
          ),
        ],
      ),
    );
  }
}
