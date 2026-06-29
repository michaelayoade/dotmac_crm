import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/api/token_store.dart' show LoginMode;
import '../features/auth/auth_state.dart';
import '../features/auth/login_screen.dart';
import '../features/auth/mfa_screen.dart';
import '../features/jobs/job_detail_screen.dart';
import '../features/location/location_tracking_controller.dart';
import '../features/materials/materials_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/schedule/schedule_screen.dart';
import '../features/today/map_screen.dart';
import '../features/today/today_screen.dart';
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
              GoRoute(path: '/map', builder: (_, _) => const MapScreen()),
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

class _AppShell extends StatelessWidget {
  const _AppShell({required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: LocationTrackingHost(child: shell),
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: shell.goBranch,
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.assignment_outlined),
            label: 'Today',
          ),
          NavigationDestination(icon: Icon(Icons.map_outlined), label: 'Map'),
          NavigationDestination(
            icon: Icon(Icons.calendar_today_outlined),
            label: 'Schedule',
          ),
          NavigationDestination(
            icon: Icon(Icons.inventory_2_outlined),
            label: 'Materials',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            label: 'Profile',
          ),
        ],
      ),
    );
  }
}
