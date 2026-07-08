import 'package:flutter/material.dart';

/// Shared visual language ported from the Stitch inspiration package.
abstract final class AppColors {
  static const primary = Color(0xFF004AC6);
  static const primaryContainer = Color(0xFF2563EB);
  static const primarySoft = Color(0xFFD0E1FB);
  static const primaryFixed = Color(0xFFDBE1FF);

  static const secondary = Color(0xFF505F76);
  static const tertiary = Color(0xFF6B6E70);
  static const accent = Color(0xFF2E65CF);

  static const success = Color(0xFF2C7A54);
  static const warning = Color(0xFFD97706);
  static const error = Color(0xFFBA1A1A);
  static const errorSoft = Color(0xFFFFDAD6);

  static const surface = Color(0xFFFAF8FF);
  static const surfaceLowest = Color(0xFFFFFFFF);
  static const surfaceLow = Color(0xFFF3F3FE);
  static const surfaceContainer = Color(0xFFEDEDF9);
  static const surfaceHigh = Color(0xFFE7E7F3);
  static const surfaceHighest = Color(0xFFE1E2ED);

  static const ink = Color(0xFF191B23);
  static const inkSoft = Color(0xFF434655);
  static const inkFaint = Color(0xFF737686);
  static const outline = Color(0xFFC3C6D7);

  static const darkSurface = Color(0xFF10131A);
  static const darkCard = Color(0xFF171B24);
  static const darkContainer = Color(0xFF212635);
  static const darkInk = Color(0xFFF0F0FB);
  static const darkInkSoft = Color(0xFFC8CCDA);
  static const darkOutline = Color(0xFF3A4154);

  // Backward-compatible aliases for legacy widgets that still compile
  // against the old token names.
  static const primaryDeep = primaryContainer;
  static const surfaceLight = surfaceLowest;
  static const surfaceDark = darkCard;
  static const lineLight = outline;
  static const lineDark = darkOutline;
  static const inkDark = darkInk;
  static const inkSoftDark = darkInkSoft;
  static const inkFaintDark = darkInkSoft;

  static const workTypeColors = <String, Color>{
    'install': Color(0xFF6B7280),
    'repair': primary,
    'survey': Color(0xFF2563EB),
    'maintenance': primary,
    'disconnect': tertiary,
    'other': tertiary,
  };

  static const statusColors = <String, Color>{
    'scheduled': Color(0xFF7C8296),
    'dispatched': primary,
    'accepted': primary,
    'en_route': Color(0xFF2563EB),
    'in_progress': primary,
    'paused': warning,
    'completed': success,
    'hold': warning,
    'canceled': tertiary,
    'draft': tertiary,
    'submitted': primary,
    'approved': success,
    'paid': success,
    'rejected': error,
  };

  static const _statusLabels = <String, String>{
    'scheduled': 'Scheduled',
    'dispatched': 'Assigned',
    'accepted': 'Accepted',
    'en_route': 'En route',
    'in_progress': 'Active',
    'paused': 'Paused',
    'completed': 'Completed',
    'hold': 'On hold',
    'canceled': 'Canceled',
    'draft': 'Draft',
    'submitted': 'Submitted',
    'approved': 'Approved',
    'paid': 'Paid',
    'rejected': 'Rejected',
  };

  static Color workType(String type) =>
      workTypeColors[type] ?? workTypeColors['other']!;

  static Color status(String status) =>
      statusColors[status] ?? statusColors['scheduled']!;

  static String statusLabel(String status) =>
      _statusLabels[status] ??
      status.replaceAll('_', ' ').replaceFirstMapped(
            RegExp(r'^\w'),
            (m) => m[0]!.toUpperCase(),
          );
}

abstract final class AppSpace {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 16.0;
  static const lg = 24.0;
  static const xl = 32.0;
  static const page = 20.0;
}

abstract final class AppRadii {
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 20.0;
  static const full = 999.0;

  static const chip = sm;
  static const control = md;
  static const tile = md;
  static const card = md;
  static const bigCard = lg;
  static const pill = full;
}

abstract final class AppSizes {
  static const headerHeight = 64.0;
  static const touchTarget = 48.0;
  static const fabSize = 56.0;

  static const primaryTouchTarget = fabSize;
}

List<BoxShadow> appSoftShadow(bool isDark) => [
  BoxShadow(
    color: (isDark ? Colors.black : AppColors.primary).withValues(
      alpha: isDark ? 0.18 : 0.08,
    ),
    blurRadius: 20,
    offset: const Offset(0, 6),
  ),
];

Color appSurface(BuildContext context) => Theme.of(context).brightness ==
        Brightness.dark
    ? AppColors.darkCard
    : AppColors.surfaceLowest;

Color appCanvas(BuildContext context) => Theme.of(context).brightness ==
        Brightness.dark
    ? AppColors.darkSurface
    : AppColors.surface;

Color appOutline(BuildContext context) => Theme.of(context).brightness ==
        Brightness.dark
    ? AppColors.darkOutline
    : AppColors.outline;

Color appMutedText(BuildContext context) => Theme.of(context).brightness ==
        Brightness.dark
    ? AppColors.darkInkSoft
    : AppColors.inkSoft;

TextTheme _textTheme(Color ink, Color muted) {
  return TextTheme(
    displaySmall: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w700,
      fontSize: 32,
      height: 1.25,
      letterSpacing: -0.64,
      color: ink,
    ),
    headlineMedium: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w700,
      fontSize: 24,
      height: 1.33,
      letterSpacing: -0.24,
      color: ink,
    ),
    headlineSmall: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w700,
      fontSize: 20,
      height: 1.4,
      letterSpacing: -0.2,
      color: ink,
    ),
    titleLarge: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w600,
      fontSize: 16,
      height: 1.5,
      color: ink,
    ),
    titleMedium: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w600,
      fontSize: 16,
      height: 1.5,
      color: ink,
    ),
    titleSmall: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w600,
      fontSize: 14,
      height: 1.35,
      color: ink,
    ),
    bodyLarge: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w400,
      fontSize: 16,
      height: 1.625,
      color: ink,
    ),
    bodyMedium: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w400,
      fontSize: 14,
      height: 1.57,
      color: muted,
    ),
    bodySmall: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w500,
      fontSize: 11,
      height: 1.27,
      color: muted,
    ),
    labelLarge: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w600,
      fontSize: 12,
      height: 1.33,
      letterSpacing: 0.6,
      color: muted,
    ),
    labelMedium: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w600,
      fontSize: 12,
      height: 1.33,
      letterSpacing: 0.6,
      color: muted,
    ),
    labelSmall: TextStyle(
      fontFamily: 'PlusJakartaSans',
      fontWeight: FontWeight.w500,
      fontSize: 11,
      height: 1.27,
      color: muted,
    ),
  );
}

ThemeData _base(Brightness brightness) {
  final isDark = brightness == Brightness.dark;
  final surface = isDark ? AppColors.darkCard : AppColors.surfaceLowest;
  final background = isDark ? AppColors.darkSurface : AppColors.surface;
  final outline = isDark ? AppColors.darkOutline : AppColors.outline;
  final ink = isDark ? AppColors.darkInk : AppColors.ink;
  final muted = isDark ? AppColors.darkInkSoft : AppColors.inkSoft;

  final scheme = ColorScheme(
    brightness: brightness,
    primary: AppColors.primary,
    onPrimary: Colors.white,
    primaryContainer: AppColors.primaryContainer,
    onPrimaryContainer: Colors.white,
    secondary: AppColors.secondary,
    onSecondary: Colors.white,
    secondaryContainer: AppColors.primarySoft,
    onSecondaryContainer: AppColors.secondary,
    tertiary: AppColors.tertiary,
    onTertiary: Colors.white,
    tertiaryContainer: AppColors.surfaceHigh,
    onTertiaryContainer: ink,
    error: AppColors.error,
    onError: Colors.white,
    errorContainer: AppColors.errorSoft,
    onErrorContainer: AppColors.error,
    surface: surface,
    onSurface: ink,
    onSurfaceVariant: muted,
    outline: outline,
    outlineVariant: outline.withValues(alpha: 0.6),
    shadow: Colors.black,
    scrim: Colors.black,
    inverseSurface: isDark ? AppColors.surface : AppColors.darkSurface,
    onInverseSurface: isDark ? AppColors.ink : AppColors.darkInk,
    inversePrimary: AppColors.primarySoft,
    surfaceContainerHighest: isDark
        ? AppColors.darkContainer
        : AppColors.surfaceHighest,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: background,
    fontFamily: 'PlusJakartaSans',
    textTheme: _textTheme(ink, muted),
    dividerColor: outline.withValues(alpha: 0.6),
    cardTheme: CardThemeData(
      color: surface,
      margin: EdgeInsets.zero,
      elevation: 0,
      shadowColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.md),
        side: BorderSide(color: outline.withValues(alpha: 0.3)),
      ),
    ),
    appBarTheme: AppBarTheme(
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      backgroundColor: background,
      surfaceTintColor: Colors.transparent,
      foregroundColor: ink,
      titleTextStyle: _textTheme(ink, muted).headlineSmall,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: isDark ? AppColors.darkContainer : AppColors.surfaceLow,
      hintStyle: TextStyle(color: muted.withValues(alpha: 0.85)),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: 14,
      ),
      prefixIconColor: AppColors.inkFaint,
      suffixIconColor: AppColors.inkFaint,
      labelStyle: TextStyle(color: muted),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.md),
        borderSide: BorderSide(color: outline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.md),
        borderSide: BorderSide(color: outline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.md),
        borderSide: const BorderSide(color: AppColors.primary, width: 1.6),
      ),
    ),
    chipTheme: ChipThemeData(
      backgroundColor: isDark ? AppColors.darkContainer : AppColors.surfaceHigh,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.full),
      ),
      side: BorderSide.none,
      labelStyle: _textTheme(ink, muted).labelSmall,
      selectedColor: AppColors.primary,
      secondarySelectedColor: AppColors.primary,
      checkmarkColor: Colors.white,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(AppSizes.touchTarget),
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadii.md),
        ),
        textStyle: _textTheme(ink, muted).titleSmall?.copyWith(
              color: Colors.white,
            ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(AppSizes.touchTarget),
        backgroundColor: surface,
        foregroundColor: ink,
        side: BorderSide(color: outline),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadii.md),
        ),
        textStyle: _textTheme(ink, muted).titleSmall,
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        foregroundColor: AppColors.primary,
        minimumSize: const Size.square(40),
        backgroundColor: Colors.transparent,
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 86,
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      backgroundColor: surface,
      elevation: 0,
      indicatorColor: AppColors.primarySoft,
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          color: states.contains(WidgetState.selected)
              ? AppColors.primary
              : muted,
          size: 24,
        ),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => _textTheme(ink, muted).bodySmall!.copyWith(
              color: states.contains(WidgetState.selected)
                  ? AppColors.primary
                  : muted,
            ),
      ),
    ),
    floatingActionButtonTheme: const FloatingActionButtonThemeData(
      backgroundColor: AppColors.primary,
      foregroundColor: Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(18)),
      ),
    ),
  );
}

final lightTheme = _base(Brightness.light);
final darkTheme = _base(Brightness.dark);
