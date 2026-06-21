import { API_URL } from "../config";
import React, { useState } from 'react';
import { View, TextInput, Button, Text } from 'react-native';

export default function LoginScreen({ navigation }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);

  const doLogin = async () => {
    try {
      const form = new FormData();
      form.append('username', username);
      form.append('password', password);
      const resp = await fetch(`${API_URL}/login`, { method: 'POST', body: form });
      if (!resp.ok) {
        setError('Login failed');
        return;
      }
      const data = await resp.json();
      // store token in-memory for now and pass to next screen
      navigation.replace('Analyze', { user: data, token: data.token });
    } catch (e) {
      setError('Network error');
    }
  };

  return (
    <View style={{ padding: 16 }}>
      <Text>Username</Text>
      <TextInput value={username} onChangeText={setUsername} style={{ borderWidth: 1, marginBottom: 8 }} />
      <Text>Password</Text>
      <TextInput value={password} onChangeText={setPassword} secureTextEntry style={{ borderWidth: 1, marginBottom: 8 }} />
      {error && <Text style={{ color: 'red' }}>{error}</Text>}
      <Button title="Login" onPress={doLogin} />
    </View>
  );
}
