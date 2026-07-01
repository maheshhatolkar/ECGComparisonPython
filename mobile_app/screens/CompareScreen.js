import { API_URL } from "../config";
import React, { useState, useEffect } from 'react';
import { View, Text, Button, Image, TextInput } from 'react-native';

export default function CompareScreen({ route }) {
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [img, setImg] = useState(route?.params?.plot || null);

  useEffect(() => {
    if (route?.params?.plot) {
      setImg(route.params.plot);
    }
  }, [route?.params?.plot]);
  const doCompare = async () => {
    try {
      const form = new FormData();
      form.append('record_a', a);
      form.append('record_b', b);
      const resp = await fetch(`${API_URL}/compare`, { method: 'POST', body: form });
      const compareResult = await resp.json();

      const plotResp = await fetch(`${API_URL}/compare/plot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          aligned_a: compareResult.aligned_a,
          aligned_b: compareResult.aligned_b
        })
      });
      const json = await plotResp.json();
      setImg('data:image/png;base64,' + json.plot_base64);
    } catch (e) {
      console.error("Comparison failed:", e);
    }
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
