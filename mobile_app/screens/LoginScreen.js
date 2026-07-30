import { getApiUrl, setApiUrl } from "../config";
import React, { useState } from 'react';
import { View, TextInput, Button, Text, ScrollView, ActivityIndicator } from 'react-native';

export default function LoginScreen({ navigation }) {
  const [serverUrl, setServerUrlState] = useState(getApiUrl());
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const doLogin = async () => {
    setError(null);
    setLoading(true);
    setApiUrl(serverUrl);
    const targetUrl = getApiUrl();

    try {
      const form = new FormData();
      form.append('username', username);
      form.append('password', password);

      const resp = await fetch(`${targetUrl}/login`, { method: 'POST', body: form });
      if (!resp.ok) {
        let errorDetail = `Status ${resp.status}`;
        try {
          const errJson = await resp.json();
          if (errJson.detail) errorDetail = errJson.detail;
        } catch (_) {}
        if (resp.status === 403) {
          setError(`Login failed: Status 403 (Forbidden). The Server URL "${targetUrl}" is likely pointing to a router or network device instead of your FastAPI server. Please check your PC's IP address (ipconfig) and ensure the FastAPI server is running on port 8000.`);
        } else if (resp.status === 401) {
          setError(`Login failed: Invalid username or password (default admin login: username "admin", password "admin").`);
        } else if (resp.status === 422) {
          setError(`Login failed: Please enter both username and password.`);
        } else {
          setError(`Login failed: ${errorDetail}`);
        }
        setLoading(false);
        return;
      }

      const data = await resp.json();
      setLoading(false);
      navigation.replace('Analyze', { user: data, token: data.token });
    } catch (e) {
      setLoading(false);
      setError(`Network error connecting to "${targetUrl}". Ensure server is running and accessible from phone/Wi-Fi.`);
    }
  };

  return (
    <ScrollView style={{ padding: 16 }}>
      <Text style={{ fontWeight: 'bold', marginBottom: 4 }}>Server URL</Text>
      <TextInput
        value={serverUrl}
        onChangeText={setServerUrlState}
        placeholder="http://192.168.1.9:8000"
        autoCapitalize="none"
        style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 12 }}
      />

      <Text style={{ fontWeight: 'bold', marginBottom: 4 }}>Username</Text>
      <TextInput
        value={username}
        onChangeText={setUsername}
        autoCapitalize="none"
        style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 12 }}
      />

      <Text style={{ fontWeight: 'bold', marginBottom: 4 }}>Password</Text>
      <TextInput
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 12 }}
      />

      {error && (
        <Text style={{ color: 'red', marginVertical: 8, padding: 8, backgroundColor: '#ffe6e6', borderRadius: 4 }}>
          {error}
        </Text>
      )}

      {loading ? (
        <ActivityIndicator size="large" color="#0000ff" />
      ) : (
        <Button title="Login" onPress={doLogin} />
      )}
    </ScrollView>
  );
}

