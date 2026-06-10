import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'router.dart';
import 'theme.dart';

class DotmacFieldApp extends ConsumerWidget {
  const DotmacFieldApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp.router(
      title: 'DotMac Field',
      theme: lightTheme,
      darkTheme: darkTheme,
      routerConfig: buildRouter(),
    );
  }
}
