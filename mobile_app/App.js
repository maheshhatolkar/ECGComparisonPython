import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import LoginScreen from './screens/LoginScreen';
import AnalyzeScreen from './screens/AnalyzeScreen';
import RecordsScreen from './screens/RecordsScreen';
import CompareScreen from './screens/CompareScreen';
import AdminAnalysisScreen from './screens/AdminAnalysisScreen';

const Stack = createNativeStackNavigator();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator initialRouteName="Login">
        <Stack.Screen name="Login" component={LoginScreen} />
        <Stack.Screen name="Analyze" component={AnalyzeScreen} />
        <Stack.Screen name="Records" component={RecordsScreen} />
        <Stack.Screen name="Compare" component={CompareScreen} />
        <Stack.Screen name="AdminAnalysis" component={AdminAnalysisScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
