import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../features/profile/profile_screen.dart';
import '../features/schedule/schedule_screen.dart';
import '../features/today/map_screen.dart';
import '../features/today/today_screen.dart';

/// App shell: 4-tab bottom navigation per the visual plan.
/// Auth and job-detail routes are layered on in later tasks.
GoRouter buildRouter() {
  return GoRouter(
    initialLocation: '/today',
    routes: [
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) => _AppShell(shell: shell),
        branches: [
          StatefulShellBranch(routes: [
            GoRoute(path: '/today', builder: (_, _) => const TodayScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/map', builder: (_, _) => const MapScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/schedule', builder: (_, _) => const ScheduleScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/profile', builder: (_, _) => const ProfileScreen()),
          ]),
        ],
      ),
    ],
  );
}

class _AppShell extends StatelessWidget {
  const _AppShell({required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: shell.goBranch,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.assignment_outlined), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.map_outlined), label: 'Map'),
          NavigationDestination(icon: Icon(Icons.calendar_today_outlined), label: 'Schedule'),
          NavigationDestination(icon: Icon(Icons.person_outline), label: 'Profile'),
        ],
      ),
    );
  }
}
