import React, { useState } from 'react';
import { View, Text, Button, Image, ActivityIndicator, ScrollView } from 'react-native';
import * as ImagePicker from 'expo-image-picker';

export default function AnalyzeScreen({ route }) {
  const { user } = route.params || {};
  const [imageUri, setImageUri] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const pickImage = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({ base64: false, quality: 0.8 });
    if (!res.cancelled) {
      setImageUri(res.uri);
    }
  };

  const analyze = async () => {
    if (!imageUri) return;
    setLoading(true);
    const form = new FormData();
    const filename = imageUri.split('/').pop();
    const match = /\.(\w+)$/.exec(filename);
    const ext = match ? match[1] : 'jpg';
    form.append('file', { uri: imageUri, name: filename, type: `image/${ext}` });
    form.append('pixels_per_mm', '20.0');
    form.append('prominence', '0.5');
    try {
      // include token in header if available
      const headers = {};
      if (route.params?.token) headers['Authorization'] = `Bearer ${route.params.token}`;
      const resp = await fetch('http://10.0.2.2:8000/analyze', { method: 'POST', body: form, headers });
      const json = await resp.json();
      setResult(json);
    } catch (e) {
      setResult({ error: 'Network error' });
    }
    setLoading(false);
  };

  return (
    <ScrollView style={{ padding: 16 }}>
      <Text>Logged in as: {user?.username} ({user?.role})</Text>
      <Button title="Pick Image" onPress={pickImage} />
      {imageUri && <Image source={{ uri: imageUri }} style={{ width: 300, height: 300, marginVertical: 8 }} />}
      <Button title="Analyze" onPress={analyze} />
      {loading && <ActivityIndicator />}
      {result && (
        <View style={{ marginTop: 16 }}>
          <Text>Result:</Text>
          <Text>{JSON.stringify(result).slice(0, 1000)}</Text>
          {result.time_ms && (
            <Button title="View waveform" onPress={async () => {
              const form = new FormData();
              form.append('analysis', JSON.stringify(result));
              const resp = await fetch('http://10.0.2.2:8000/analysis/plot', { method: 'POST', body: form });
              const j = await resp.json();
              navigation.navigate('Compare', { plot: 'data:image/png;base64,' + j.image_base64 });
            }} />
          )}
        </View>
      )}
    </ScrollView>
  );
}
