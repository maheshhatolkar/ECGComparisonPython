import { API_URL } from "../config";
import React, { useState } from 'react';
import { View, Text, Button, Image, TextInput } from 'react-native';

export default function CompareScreen() {
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [img, setImg] = useState(null);
  const doCompare = async () => {
    const form = new FormData();
    form.append('record_a', a);
    form.append('record_b', b);
    const resp = await fetch(`${API_URL}/compare/plot`, { method: 'POST', body: form });
    const json = await resp.json();
    setImg('data:image/png;base64,' + json.image_base64);
  };
  return (
    <View style={{ padding: 16 }}>
      <TextInput placeholder="Record A id" value={a} onChangeText={setA} style={{ borderWidth: 1, marginBottom: 8 }} />
      <TextInput placeholder="Record B id" value={b} onChangeText={setB} style={{ borderWidth: 1, marginBottom: 8 }} />
      <Button title="Compare" onPress={doCompare} />
      {img && <Image source={{ uri: img }} style={{ width: 320, height: 200, marginTop: 8 }} />}
    </View>
  );
}
