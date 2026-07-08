import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'theme.dart';
import '../core/api/token_store.dart' show LoginMode;
import '../features/auth/auth_state.dart';
import '../features/auth/login_screen.dart';
import '../features/auth/mfa_screen.dart';
import '../features/customers/customer_models.dart';
import '../features/customers/customer_lookup_screen.dart';
import '../features/expenses/expenses_screen.dart';
import '../features/jobs/job_detail_screen.dart';
import '../features/location/location_tracking_controller.dart';
import '../features/materials/materials_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/schedule/schedule_screen.dart';
import '../features/today/map_screen.dart';
import '../features/today/today_screen.dart';
import '../features/vendor/vendor_map_screen.dart';
import '../features/vendor/vendor_screens.dart';

/// App shell: login gate + 4-tab bottom navigation per the visual plan.
GoRouter buildRouter(Ref ref) {
  final listenable = ValueNotifier(0);
  ref.listen(authControllerProvider, (_, _) => listenable.value++);

  return GoRouter(
    initialLocation: '/today',
    refreshListenable: listenable,
    redirect: (context, state) {
      final auth = ref.read(authControllerProvider);
      final atRestore = state.matchedLocation == '/restore';
      final atLogin = state.matchedLocation == '/login';
      final atMfa = state.matchedLocation == '/mfa';
      final atUpgrade = state.matchedLocation == '/upgrade';
      return switch (auth) {
        RestoringSession() => atRestore ? null : '/restore',
        Unauthenticated() => atLogin ? null : '/login',
        AwaitingMfa() => atMfa ? null : '/mfa',
        UpgradeRequired() => atUpgrade ? null : '/upgrade',
        Authenticated() =>
          (atRestore || atLogin || atMfa || atUpgrade) ? '/today' : null,
      };
    },
    routes: [
      GoRoute(path: '/restore', builder: (_, _) => const _RestoreScreen()),
      GoRoute(path: '/login', builder: (_, _) => const LoginScreen()),
      GoRoute(path: '/mfa', builder: (_, _) => const MfaScreen()),
      GoRoute(
        path: '/upgrade',
        builder: (_, _) => const UpgradeRequiredScreen(),
      ),
      GoRoute(
        path: '/jobs/:id',
        builder: (_, state) =>
            JobDetailScreen(jobId: state.pathParameters['id']!),
      ),
      GoRoute(
        path: '/materials/new',
        builder: (_, state) => NewMaterialRequestScreen(
          initialWorkOrderId: state.uri.queryParameters['workOrderId'],
          initialWorkOrderLabel: state.uri.queryParameters['workOrderLabel'],
        ),
      ),
      GoRoute(
        path: '/materials/:id',
        builder: (_, state) =>
            MaterialRequestDetailScreen(id: state.pathParameters['id']!),
      ),
      GoRoute(
        path: '/expenses/new',
        builder: (_, state) => NewExpenseRequestScreen(
          initialWorkOrderId: state.uri.queryParameters['workOrderId'],
          initialWorkOrderLabel: state.uri.queryParameters['workOrderLabel'],
        ),
      ),
      GoRoute(
        path: '/expenses/:id',
        builder: (_, state) =>
            ExpenseRequestDetailScreen(id: state.pathParameters['id']!),
      ),
      GoRoute(
        path: '/customers/:id',
        builder: (_, state) => CustomerDetailScreen(
          customer: state.extra is CustomerLookupResult
              ? state.extra! as CustomerLookupResult
              : CustomerLookupResult.fromQuery(state.uri.queryParameters),
        ),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) => _AppShell(shell: shell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/today', builder: (_, _) => const _HomeSwitch()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/map', builder: (_, _) => const _MapSwitch()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/schedule',
                builder: (_, _) => const ScheduleScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/materials',
                builder: (_, _) => const MaterialsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/expenses',
                builder: (_, _) => const ExpensesScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/customers',
                builder: (_, _) => const CustomerLookupScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/profile',
                builder: (_, _) => const ProfileScreen(),
              ),
            ],
          ),
        ],
      ),
    ],
  );
}

final routerProvider = Provider<GoRouter>(buildRouter);

class _RestoreScreen extends StatelessWidget {
  const _RestoreScreen();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(body: Center(child: CircularProgressIndicator()));
  }
}

/// Vendor crews get their Projects module where techs see Today.
class _HomeSwitch extends ConsumerWidget {
  const _HomeSwitch();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    if (auth is Authenticated && auth.mode == LoginMode.vendor) {
      return const VendorProjectsScreen();
    }
    return const TodayScreen();
  }
}

/// The Map tab shows vendors their vendor-scoped nearby plant; techs get the
/// full technician map (job pins + editable assets).
class _MapSwitch extends ConsumerWidget {
  const _MapSwitch();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    if (auth is Authenticated && auth.mode == LoginMode.vendor) {
      return const VendorMapScreen();
    }
    return const MapScreen();
  }
}

/// A bottom-nav destination bound to a shell branch index. The visible set
/// differs by login mode, but every entry maps to the same fixed branch so
/// `shell.goBranch` stays correct regardless of what's shown.
class _NavItem {
  const _NavItem(this.branchIndex, this.icon, this.label);
  final int branchIndex;
  final IconData icon;
  final String label;
}

// Branch order (see StatefulShellRoute above):
// 0 Today/Projects · 1 Map · 2 Schedule · 3 Materials · 4 Expenses ·
// 5 Customers · 6 Profile
const _staffNav = [
  _NavItem(0, Icons.assignment_outlined, 'Today'),
  _NavItem(1, Icons.map_outlined, 'Map'),
  _NavItem(2, Icons.calendar_today_outlined, 'Schedule'),
  _NavItem(3, Icons.inventory_2_outlined, 'Materials'),
  _NavItem(4, Icons.receipt_long_outlined, 'Expenses'),
];

// Vendors get the tabs backed by vendor-aware endpoints: Projects, the
// vendor-scoped Map (nearby plant), and Profile. Schedule / Materials /
// Expenses / Customers are require_technician and would 403, so they stay
// hidden.
const _vendorNav = [
  _NavItem(0, Icons.assignment_outlined, 'Projects'),
  _NavItem(1, Icons.map_outlined, 'Map'),
];

class _AppShell extends ConsumerWidget {
  const _AppShell({required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final isVendor = auth is Authenticated && auth.mode == LoginMode.vendor;
    final items = isVendor ? _vendorNav : _staffNav;
    final selected = items.indexWhere(
      (i) => i.branchIndex == shell.currentIndex,
    );
    final theme = Theme.of(context);
    final current = selected < 0 ? 0 : selected;
    return Scaffold(
      body: LocationTrackingHost(child: shell),
      bottomNavigationBar: Container(
        decoration: BoxDecoration(
          color: appSurface(context),
          border: Border(top: BorderSide(color: appOutline(context))),
          boxShadow: appSoftShadow(theme.brightness == Brightness.dark),
          borderRadius: const BorderRadius.vertical(
            top: Radius.circular(AppRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(8, 10, 8, 16),
        child: SafeArea(
          top: false,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              for (var index = 0; index < items.length; index++)
                Expanded(
                  child: _ShellNavButton(
                    item: items[index],
                    selected: index == current,
                    onTap: () => shell.goBranch(items[index].branchIndex),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ShellNavButton extends StatelessWidget {
  const _ShellNavButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final _NavItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final muted = appMutedText(context);
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        margin: const EdgeInsets.symmetric(horizontal: 4),
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.primarySoft : Colors.transparent,
          borderRadius: BorderRadius.circular(AppRadii.full),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              item.icon,
              color: selected ? AppColors.primary : muted,
              size: 24,
            ),
            const SizedBox(height: 4),
            Text(
              item.label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: selected ? AppColors.primary : muted,
                  ),
            ),
          ],
        ),
      ),
    );
  }
}
