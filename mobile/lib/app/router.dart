import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/api/token_store.dart' show LoginMode;
import '../features/auth/auth_state.dart';
import '../features/auth/login_screen.dart';
import '../features/auth/mfa_screen.dart';
import '../features/customers/customer_lookup_screen.dart';
import '../features/jobs/job_detail_screen.dart';
import '../features/location/location_tracking_controller.dart';
import '../features/materials/materials_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/sales/sales_screen.dart';
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
      final atLogin = state.matchedLocation == '/login';
      final atMfa = state.matchedLocation == '/mfa';
      final atUpgrade = state.matchedLocation == '/upgrade';
      return switch (auth) {
        Unauthenticated() => atLogin ? null : '/login',
        AwaitingMfa() => atMfa ? null : '/mfa',
        UpgradeRequired() => atUpgrade ? null : '/upgrade',
        Authenticated() => (atLogin || atMfa || atUpgrade) ? '/today' : null,
      };
    },
    routes: [
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
        ),
      ),
      GoRoute(
        path: '/materials/:id',
        builder: (_, state) =>
            MaterialRequestDetailScreen(id: state.pathParameters['id']!),
      ),
      GoRoute(
        path: '/sales/new',
        builder: (_, state) => NewSalesOrderScreen(
          initialCustomerId: state.uri.queryParameters['customerId'],
          initialCustomerLabel: state.uri.queryParameters['customerLabel'],
          initialCustomerRef: state.uri.queryParameters['customerRef'],
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
                path: '/customers',
                builder: (_, _) => const CustomerLookupScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/sales', builder: (_, _) => const SalesScreen()),
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
// 0 Today/Projects · 1 Map · 2 Schedule · 3 Materials · 4 Customers · 5 Sales · 6 Profile
const _staffNav = [
  _NavItem(0, Icons.assignment_outlined, 'Today'),
  _NavItem(1, Icons.map_outlined, 'Map'),
  _NavItem(2, Icons.calendar_today_outlined, 'Schedule'),
  _NavItem(3, Icons.inventory_2_outlined, 'Materials'),
  _NavItem(4, Icons.people_alt_outlined, 'Customers'),
  _NavItem(5, Icons.receipt_long_outlined, 'Sales'),
  _NavItem(6, Icons.person_outline, 'Profile'),
];

// Vendors get the tabs backed by vendor-aware endpoints: Projects, the
// vendor-scoped Map (nearby plant), and Profile. Schedule / Materials /
// Customers / Sales are require_technician and would 403, so they stay hidden.
const _vendorNav = [
  _NavItem(0, Icons.assignment_outlined, 'Projects'),
  _NavItem(1, Icons.map_outlined, 'Map'),
  _NavItem(6, Icons.person_outline, 'Profile'),
];

class _AppShell extends ConsumerWidget {
  const _AppShell({required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final isVendor = auth is Authenticated && auth.mode == LoginMode.vendor;
    final items = isVendor ? _vendorNav : _staffNav;
    // Map the active branch to its position in the visible set (0 if the
    // current branch is hidden for this mode).
    final selected = items.indexWhere((i) => i.branchIndex == shell.currentIndex);
    return Scaffold(
      body: LocationTrackingHost(child: shell),
      bottomNavigationBar: NavigationBar(
        selectedIndex: selected < 0 ? 0 : selected,
        onDestinationSelected: (pos) => shell.goBranch(items[pos].branchIndex),
        destinations: [
          for (final item in items)
            NavigationDestination(icon: Icon(item.icon), label: item.label),
        ],
      ),
    );
  }
}
